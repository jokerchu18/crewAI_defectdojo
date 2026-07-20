from pathlib import Path
import os


BASE_DIR = Path(__file__).resolve().parents[2]
ENV_PATH = BASE_DIR / ".env"


def load_env_file(env_path: Path) -> None:
    if not env_path.exists():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


def get_bool_env(key: str, default: bool) -> bool:
    value = os.getenv(key)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


load_env_file(ENV_PATH)


class Settings:
    def __init__(self):
        self.defectdojo_base_url = os.getenv("DEFECTDOJO_BASE_URL", "http://localhost:8080")
        self.defectdojo_api_key = os.getenv("DEFECTDOJO_API_KEY", "")
        self.defectdojo_engagement_id = int(os.getenv("DEFECTDOJO_ENGAGEMENT_ID", "1"))

        self.default_scan_type = os.getenv("DEFAULT_SCAN_TYPE", "SARIF")
        self.default_scan_file_path = os.getenv(
            "DEFAULT_SCAN_FILE_PATH",
            r"D:\github\crewAI_defectdojo\sample_reports\sample_multi.sarif",
        )

        self.crew_verbose = get_bool_env("CREW_VERBOSE", True)

        self.redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")
        self.session_redis_prefix = os.getenv(
            "SESSION_REDIS_PREFIX",
            "defectdojo:session",
        )
        self.session_ttl_seconds = int(
            os.getenv("SESSION_TTL_SECONDS", "86400")
        )
        self.session_refresh_ttl_on_read = get_bool_env(
            "SESSION_REFRESH_TTL_ON_READ",
            True,
        )
        self.session_history_max_messages = int(
            os.getenv("SESSION_HISTORY_MAX_MESSAGES", "200")
        )

        self.chat_database_url = os.getenv(
            "CHAT_DATABASE_URL",
            "postgresql://chat:chatpass@localhost:5433/chat_history",
        )
        self.chat_database_pool_size = int(
            os.getenv("CHAT_DATABASE_POOL_SIZE", "5")
        )
        self.chat_database_timeout_seconds = float(
            os.getenv("CHAT_DATABASE_TIMEOUT_SECONDS", "5")
        )
        self.redis_socket_timeout_seconds = float(
            os.getenv("REDIS_SOCKET_TIMEOUT_SECONDS", "5")
        )

        self.web_upload_dir = Path(
            os.getenv("WEB_UPLOAD_DIR", str(BASE_DIR / "data" / "uploads"))
        ).expanduser()
        if not self.web_upload_dir.is_absolute():
            self.web_upload_dir = (BASE_DIR / self.web_upload_dir).resolve()
        self.web_upload_max_bytes = int(
            os.getenv("WEB_UPLOAD_MAX_BYTES", str(20 * 1024 * 1024))
        )

        if self.session_ttl_seconds <= 0:
            raise ValueError("SESSION_TTL_SECONDS must be greater than 0.")
        if self.session_history_max_messages <= 0:
            raise ValueError(
                "SESSION_HISTORY_MAX_MESSAGES must be greater than 0."
            )
        if self.chat_database_pool_size <= 0:
            raise ValueError("CHAT_DATABASE_POOL_SIZE must be greater than 0.")
        if self.chat_database_timeout_seconds <= 0:
            raise ValueError(
                "CHAT_DATABASE_TIMEOUT_SECONDS must be greater than 0."
            )
        if self.redis_socket_timeout_seconds <= 0:
            raise ValueError(
                "REDIS_SOCKET_TIMEOUT_SECONDS must be greater than 0."
            )
        if self.web_upload_max_bytes <= 0:
            raise ValueError("WEB_UPLOAD_MAX_BYTES must be greater than 0.")


settings = Settings()
