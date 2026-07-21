import json
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from defectdojo_crewai.agents.router import router_agent
from defectdojo_crewai.config.settings import settings
from defectdojo_crewai.models.schemas import WorkflowPlan
from defectdojo_crewai.services.routing_service import parse_workflow_plan


@dataclass(frozen=True)
class RouteCase:
    name: str
    message: str
    intents: list[str]
    fields: dict[int, dict[str, Any]] = field(default_factory=dict)
    sequential: bool = False


CASES = [
    RouteCase(
        "query_product",
        "查询 Product 1 下的所有活跃漏洞。",
        ["query_findings"],
        {0: {"product_id": 1}},
    ),
    RouteCase(
        "query_test",
        "列出 test_id 42 这次扫描发现的漏洞。",
        ["query_findings"],
        {0: {"test_id": 42}},
    ),
    RouteCase(
        "triage",
        "对 test 12 的全部漏洞进行分诊。",
        ["triage"],
        {0: {"test_id": 12}},
    ),
    RouteCase(
        "deduplication",
        "给 test_id=15 做漏洞去重。",
        ["deduplication"],
        {0: {"test_id": 15}},
    ),
    RouteCase(
        "remediation",
        "为 Product 3 制定漏洞修复计划和 SLA。",
        ["remediation"],
        {0: {"product_id": 3}},
    ),
    RouteCase(
        "risk_acceptance",
        "评估 Product 4 的 Medium 和 Low 漏洞是否可以风险接受。",
        ["risk_acceptance"],
        {0: {"product_id": 4}},
    ),
    RouteCase(
        "verification",
        "验证 finding 101、102 和 103 是否已经修复。",
        ["verification"],
        {0: {"finding_ids": [101, 102, 103]}},
    ),
    RouteCase(
        "import_scan",
        r"把 D:\reports\scan.sarif 以 SARIF 导入 engagement 7。",
        ["import_scan"],
        {
            0: {
                "engagement_id": 7,
                "scan_type": "SARIF",
                "file_path": r"D:\reports\scan.sarif",
            }
        },
    ),
    RouteCase(
        "unknown_chat",
        "你好，今天心情怎么样？",
        ["unknown"],
    ),
    RouteCase(
        "import_triage",
        "导入 SARIF 报告后进行漏洞分诊。",
        ["import_scan", "triage"],
        sequential=True,
    ),
    RouteCase(
        "import_dedup_triage",
        "导入扫描报告，然后去重并完成分诊。",
        ["import_scan", "deduplication", "triage"],
        sequential=True,
    ),
    RouteCase(
        "query_risk",
        "查询 Product 1 的漏洞并评估风险接受。",
        ["query_findings", "risk_acceptance"],
        {0: {"product_id": 1}},
        sequential=True,
    ),
    RouteCase(
        "query_remediation",
        "先查询 Product 8 的漏洞，再制定修复计划。",
        ["query_findings", "remediation"],
        {0: {"product_id": 8}},
        sequential=True,
    ),
    RouteCase(
        "triage_remediation",
        "先分诊 test 21，再为 Product 9 制定修复计划。",
        ["triage", "remediation"],
        {
            0: {"test_id": 21},
            1: {"product_id": 9},
        },
    ),
    RouteCase(
        "five_step_lifecycle",
        "导入报告，完成去重和分诊，为 Product 6 制定修复计划，最后评估风险接受。",
        [
            "import_scan",
            "deduplication",
            "triage",
            "remediation",
            "risk_acceptance",
        ],
        {3: {"product_id": 6}},
        sequential=True,
    ),
    RouteCase(
        "six_step_lifecycle",
        (
            r"将 D:\reports\q2.sarif 以 SARIF 导入 engagement 11，随后去重、"
            "分诊、为 Product 6 制定修复计划、验证修复结果，最后评估风险接受。"
        ),
        [
            "import_scan",
            "deduplication",
            "triage",
            "remediation",
            "verification",
            "risk_acceptance",
        ],
        {
            0: {
                "engagement_id": 11,
                "scan_type": "SARIF",
                "file_path": r"D:\reports\q2.sarif",
            },
            3: {"product_id": 6},
        },
        sequential=True,
    ),
    RouteCase(
        "risk_must_be_last",
        "先评估 Product 7 的风险接受，然后查询相关漏洞。",
        ["query_findings", "risk_acceptance"],
        {0: {"product_id": 7}},
        sequential=True,
    ),
    RouteCase(
        "nessus_import",
        r"把 Nessus 报告 C:\scan\nessus.csv 导入 engagement 22。",
        ["import_scan"],
        {
            0: {
                "engagement_id": 22,
                "scan_type": ("Nessus", "Nessus Scan"),
                "file_path": r"C:\scan\nessus.csv",
            }
        },
    ),
    RouteCase(
        "colloquial_chain",
        "test 88 先查查有没有重复项，再把漏洞都分个诊。",
        ["deduplication", "triage"],
        {0: {"test_id": 88}},
        sequential=True,
    ),
    RouteCase(
        "unsupported_close",
        "关闭 finding 99，并给负责人发送邮件。",
        ["unknown"],
    ),
]


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) == 3 and sys.argv[1] == "--rescore":
        rescore_saved_results(Path(sys.argv[2]))
        return

    settings.crew_verbose = False
    router_agent.verbose = False

    results = []
    for index, case in enumerate(CASES, start=1):
        started = time.perf_counter()
        try:
            plan = parse_workflow_plan(case.message)
            elapsed = time.perf_counter() - started
            result = score_case(case, plan)
            result["error"] = None
            result["prediction"] = plan.model_dump()
        except Exception as exc:
            elapsed = time.perf_counter() - started
            result = {
                "intent_correct": False,
                "parameters_correct": False,
                "dependencies_correct": False,
                "strict_correct": False,
                "error": f"{type(exc).__name__}: {exc}",
                "prediction": None,
            }
        result.update(
            {
                "name": case.name,
                "message": case.message,
                "expected_intents": case.intents,
                "elapsed_seconds": elapsed,
            }
        )
        results.append(result)
        status = "PASS" if result["strict_correct"] else "FAIL"
        print(
            f"[{index:02d}/{len(CASES)}] {status} {case.name}: "
            f"{elapsed:.3f}s"
        )

    summary = summarize(results)
    output = {
        "model": router_agent.llm.model,
        "case_count": len(CASES),
        "summary": summary,
        "results": results,
    }
    output_path = Path("data/benchmarks/router_benchmark.json")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print("\nSummary")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Detailed results: {output_path.resolve()}")


def rescore_saved_results(output_path: Path) -> None:
    output = json.loads(output_path.read_text(encoding="utf-8"))
    cases_by_name = {case.name: case for case in CASES}

    for result in output["results"]:
        case = cases_by_name[result["name"]]
        prediction = result.get("prediction")
        if prediction is None:
            scores = {
                "intent_correct": False,
                "parameters_correct": False,
                "dependencies_correct": False,
                "strict_correct": False,
            }
        else:
            scores = score_case(
                case,
                WorkflowPlan.model_validate(prediction),
            )
        result.update(scores)

    output["summary"] = summarize(output["results"])
    output_path.write_text(
        json.dumps(output, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps(output["summary"], ensure_ascii=False, indent=2))


def score_case(case: RouteCase, plan) -> dict[str, bool]:
    actual_intents = [step.intent for step in plan.steps]
    intent_correct = actual_intents == case.intents

    parameters_correct = len(plan.steps) == len(case.intents)
    for step_index, expected_fields in case.fields.items():
        if step_index >= len(plan.steps):
            parameters_correct = False
            continue
        step = plan.steps[step_index]
        for name, expected_value in expected_fields.items():
            actual_value = getattr(step, name)
            if isinstance(expected_value, tuple):
                matches = actual_value in expected_value
            else:
                matches = actual_value == expected_value
            if not matches:
                parameters_correct = False

    dependencies_correct = _dependencies_correct(case, plan.steps)
    return {
        "intent_correct": intent_correct,
        "parameters_correct": parameters_correct,
        "dependencies_correct": dependencies_correct,
        "strict_correct": (
            intent_correct
            and parameters_correct
            and dependencies_correct
        ),
    }


def _dependencies_correct(case: RouteCase, steps) -> bool:
    if not steps:
        return False
    if steps[0].depends_on:
        return False
    if not case.sequential:
        return all(not step.depends_on for step in steps[1:])
    return all(
        step.depends_on == [steps[index - 1].step_id]
        for index, step in enumerate(steps[1:], start=1)
    )


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    latencies = [result["elapsed_seconds"] for result in results]
    sorted_latencies = sorted(latencies)
    warm_latencies = latencies[1:] if len(latencies) > 1 else latencies
    return {
        "intent_accuracy_percent": _accuracy(results, "intent_correct"),
        "parameter_accuracy_percent": _accuracy(
            results,
            "parameters_correct",
        ),
        "dependency_accuracy_percent": _accuracy(
            results,
            "dependencies_correct",
        ),
        "strict_accuracy_percent": _accuracy(results, "strict_correct"),
        "average_latency_seconds": statistics.mean(latencies),
        "warm_average_latency_seconds": statistics.mean(warm_latencies),
        "p50_latency_seconds": statistics.median(latencies),
        "p95_latency_seconds": _percentile(sorted_latencies, 0.95),
        "min_latency_seconds": min(latencies),
        "max_latency_seconds": max(latencies),
        "max_expected_steps": max(len(case.intents) for case in CASES),
        "max_successful_steps": max(
            (
                len(result["expected_intents"])
                for result in results
                if result["strict_correct"]
            ),
            default=0,
        ),
        "error_count": sum(result["error"] is not None for result in results),
    }


def _accuracy(results: list[dict[str, Any]], field_name: str) -> float:
    correct = sum(bool(result[field_name]) for result in results)
    return round(correct / len(results) * 100, 2)


def _percentile(sorted_values: list[float], percentile: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return (
        sorted_values[lower] * (1 - fraction)
        + sorted_values[upper] * fraction
    )


if __name__ == "__main__":
    main()
