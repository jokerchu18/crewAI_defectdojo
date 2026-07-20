"""HTTP API and same-origin UI for the vulnerability lifecycle assistant."""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from uuid import uuid4

from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.models.schemas import ApprovalDecision, ChatRequest
from defectdojo_crewai.services.approval_service import (
    decide_approval,
    pending_approvals,
)
from defectdojo_crewai.services.approval_store import init_approval_store
from defectdojo_crewai.services.message_store import (
    MessageStoreError,
    clear_messages,
    close_message_store,
    get_messages,
    init_message_store,
)
from defectdojo_crewai.services.progress_service import get_progress
from defectdojo_crewai.services.routing_service import handle_chat_request
from defectdojo_crewai.services.session_service import (
    SessionStoreError,
    clear_session_context,
    get_session_context,
    init_session_store,
)


STATIC_DIR = Path(__file__).resolve().parent / "web_static"
ALLOWED_REPORT_SUFFIXES = {".sarif", ".nessus", ".xml", ".json"}
SCAN_TYPE_BY_SUFFIX = {
    ".sarif": "SARIF",
    ".nessus": "Nessus Scan",
}


@asynccontextmanager
async def lifespan(app: Starlette):
    init_approval_store()
    settings.web_upload_dir.mkdir(parents=True, exist_ok=True)
    try:
        await run_in_threadpool(init_session_store)
        app.state.redis_ready = True
    except SessionStoreError:
        app.state.redis_ready = False
    try:
        await run_in_threadpool(init_message_store)
        app.state.postgres_ready = True
    except MessageStoreError:
        app.state.postgres_ready = False
    yield
    await run_in_threadpool(close_message_store)


async def health(request: Request) -> JSONResponse:
    try:
        await run_in_threadpool(init_session_store)
        request.app.state.redis_ready = True
    except SessionStoreError:
        request.app.state.redis_ready = False
    try:
        await run_in_threadpool(init_message_store)
        request.app.state.postgres_ready = True
    except MessageStoreError:
        request.app.state.postgres_ready = False

    healthy = request.app.state.redis_ready and request.app.state.postgres_ready
    return JSONResponse(
        {
            "status": "ok" if healthy else "degraded",
            "service": "defectdojo-crewai",
            "redis": "ok" if request.app.state.redis_ready else "unavailable",
            "postgres": "ok" if request.app.state.postgres_ready else "unavailable",
        },
        status_code=200 if healthy else 503,
    )


async def chat(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    try:
        chat_request = ChatRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    response = await run_in_threadpool(handle_chat_request, chat_request)
    return JSONResponse(response.model_dump(mode="json"))


async def upload_report(request: Request) -> JSONResponse:
    form = await request.form(
        max_files=1,
        max_fields=10,
        max_part_size=settings.web_upload_max_bytes,
    )
    upload = form.get("file")
    if upload is None or not hasattr(upload, "filename"):
        raise HTTPException(status_code=422, detail="请在 file 字段上传扫描报告。")

    original_name = Path(upload.filename or "report").name
    suffix = Path(original_name).suffix.lower()
    if suffix not in ALLOWED_REPORT_SUFFIXES:
        allowed = ", ".join(sorted(ALLOWED_REPORT_SUFFIXES))
        raise HTTPException(
            status_code=415,
            detail=f"不支持的报告格式。允许格式: {allowed}",
        )

    settings.web_upload_dir.mkdir(parents=True, exist_ok=True)
    destination = settings.web_upload_dir / f"{uuid4().hex}{suffix}"
    total_size = 0

    try:
        with destination.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                total_size += len(chunk)
                if total_size > settings.web_upload_max_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "报告超过大小限制 "
                            f"{settings.web_upload_max_bytes // (1024 * 1024)} MB。"
                        ),
                    )
                output.write(chunk)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    return JSONResponse(
        {
            "original_name": original_name,
            "file_path": str(destination),
            "size_bytes": total_size,
            "scan_type": SCAN_TYPE_BY_SUFFIX.get(suffix),
            "message": "报告已保存。请在对话中确认导入或继续执行工作流。",
        },
        status_code=201,
    )


async def list_approvals(_: Request) -> JSONResponse:
    approvals = await run_in_threadpool(pending_approvals)
    return JSONResponse({"items": approvals, "count": len(approvals)})


async def approval_decision(request: Request) -> JSONResponse:
    payload = await _json_body(request)
    payload["approval_id"] = request.path_params["approval_id"]
    try:
        decision = ApprovalDecision.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    approval = await run_in_threadpool(decide_approval, decision)
    return JSONResponse(approval)


async def session_progress(request: Request) -> JSONResponse:
    progress = get_progress(request.path_params["session_id"])
    if progress is None:
        return JSONResponse({"phase": "idle", "status": "idle", "steps": []})
    return JSONResponse(progress)


async def session_context(request: Request) -> JSONResponse:
    context = await run_in_threadpool(
        get_session_context,
        request.path_params["session_id"],
    )
    return JSONResponse(context.model_dump(mode="json"))


async def session_messages(request: Request) -> JSONResponse:
    messages = await run_in_threadpool(
        get_messages,
        request.path_params["session_id"],
    )
    return JSONResponse({"items": messages, "count": len(messages)})


async def delete_session(request: Request) -> JSONResponse:
    session_id = request.path_params["session_id"]
    await run_in_threadpool(clear_session_context, session_id)
    await run_in_threadpool(clear_messages, session_id)
    return JSONResponse({"status": "cleared"})


async def _json_body(request: Request) -> dict:
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象。") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是 JSON 对象。")
    return payload


async def session_error(_: Request, exc: SessionStoreError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=503)


async def value_error(_: Request, exc: ValueError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=400)


async def server_error(_: Request, exc: Exception) -> JSONResponse:
    logging.exception("Unhandled web API error", exc_info=exc)
    return JSONResponse(
        {"detail": "服务执行失败，请查看服务日志。"},
        status_code=500,
    )


app = Starlette(
    debug=False,
    lifespan=lifespan,
    routes=[
        Route("/api/health", health, methods=["GET"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/uploads", upload_report, methods=["POST"]),
        Route("/api/approvals", list_approvals, methods=["GET"]),
        Route(
            "/api/approvals/{approval_id}/decision",
            approval_decision,
            methods=["POST"],
        ),
        Route("/api/sessions/{session_id}/progress", session_progress, methods=["GET"]),
        Route("/api/sessions/{session_id}/messages", session_messages, methods=["GET"]),
        Route("/api/sessions/{session_id}", session_context, methods=["GET"]),
        Route("/api/sessions/{session_id}", delete_session, methods=["DELETE"]),
        Mount("/", app=StaticFiles(directory=STATIC_DIR, html=True), name="ui"),
    ],
    exception_handlers={
        SessionStoreError: session_error,
        MessageStoreError: session_error,
        ValueError: value_error,
        Exception: server_error,
    },
)
