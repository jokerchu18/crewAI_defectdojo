import argparse

from defectdojo_crewai.utils.logging_config import configure_logging


def main():
    configure_logging()
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        nargs="?",
        choices=["chat", "web"],
        default="chat",
        help="chat 启动终端交互；web 启动浏览器界面与 HTTP API。",
    )
    args = parser.parse_args()

    if args.mode == "chat":
        from defectdojo_crewai.chat import run_chat

        run_chat()
        return

    if args.mode == "web":
        import uvicorn

        uvicorn.run(
            "defectdojo_crewai.web:app",
            host="127.0.0.1",
            port=8000,
            reload=False,
            log_config=None,
        )
        return

if __name__ == "__main__":
    main()
