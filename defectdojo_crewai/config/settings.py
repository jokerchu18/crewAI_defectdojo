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
        self.defectdojo_tool_max_concurrency = int(
            os.getenv("DEFECTDOJO_TOOL_MAX_CONCURRENCY", "8")
        )

        self.default_scan_type = os.getenv("DEFAULT_SCAN_TYPE", "SARIF")
        self.default_scan_file_path = os.getenv(
            "DEFAULT_SCAN_FILE_PATH",
            r"D:\github\crewAI_defectdojo\sample_reports\sample_multi.sarif",
        )

        self.knowledge_enabled = get_bool_env("KNOWLEDGE_ENABLED", True)
        self.knowledge_base_dir = Path(
            os.getenv(
                "KNOWLEDGE_BASE_DIR",
                str(BASE_DIR / "data" / "knowledge"),
            )
        ).expanduser()
        if not self.knowledge_base_dir.is_absolute():
            self.knowledge_base_dir = (
                BASE_DIR / self.knowledge_base_dir
            ).resolve()
        self.knowledge_top_k = int(os.getenv("KNOWLEDGE_TOP_K", "4"))
        self.knowledge_max_chars = int(
            os.getenv("KNOWLEDGE_MAX_CHARS", "6000")
        )
        configured_embedding_model = os.getenv("EMBEDDING_MODEL")
        configured_embedding_base_url = os.getenv("EMBEDDING_BASE_URL")
        default_embedding_provider = (
            "openai"
            if configured_embedding_model or configured_embedding_base_url
            else "fastembed"
        )
        self.embedding_provider = os.getenv(
            "EMBEDDING_PROVIDER",
            default_embedding_provider,
        ).strip().lower()
        default_embedding_model = (
            "BAAI/bge-small-zh-v1.5"
            if self.embedding_provider == "fastembed"
            else "text-embedding-3-small"
        )
        self.embedding_model = os.getenv(
            "EMBEDDING_MODEL",
            default_embedding_model,
        )
        self.embedding_cache_dir = Path(
            os.getenv(
                "EMBEDDING_CACHE_DIR",
                str(BASE_DIR / "data" / "models" / "fastembed"),
            )
        ).expanduser()
        if not self.embedding_cache_dir.is_absolute():
            self.embedding_cache_dir = (
                BASE_DIR / self.embedding_cache_dir
            ).resolve()
        self.openai_api_key = os.getenv(
            "EMBEDDING_API_KEY",
            os.getenv(
                "OPENAI_API_KEY",
                os.getenv("api_key", ""),
            ),
        )
        self.openai_base_url = os.getenv(
            "EMBEDDING_BASE_URL",
            os.getenv(
                "OPENAI_BASE_URL",
                os.getenv("base_url"),
            ),
        )
        self.qdrant_url = os.getenv(
            "QDRANT_URL",
            "http://localhost:6333",
        ).rstrip("/")
        self.qdrant_api_key = os.getenv("QDRANT_API_KEY") or None
        self.qdrant_collection_name = os.getenv(
            "QDRANT_COLLECTION_NAME",
            "defectdojo_knowledge",
        )
        self.qdrant_timeout_seconds = int(
            os.getenv("QDRANT_TIMEOUT_SECONDS", "30")
        )
        self.qdrant_prefer_grpc = get_bool_env(
            "QDRANT_PREFER_GRPC",
            False,
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
        if self.defectdojo_tool_max_concurrency <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_MAX_CONCURRENCY must be greater than 0."
            )
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
        if self.knowledge_top_k <= 0:
            raise ValueError("KNOWLEDGE_TOP_K must be greater than 0.")
        if self.knowledge_max_chars <= 0:
            raise ValueError("KNOWLEDGE_MAX_CHARS must be greater than 0.")
        if self.embedding_provider not in {"fastembed", "openai"}:
            raise ValueError(
                "EMBEDDING_PROVIDER must be 'fastembed' or 'openai'."
            )
        if not self.qdrant_url:
            raise ValueError("QDRANT_URL must not be empty.")
        if not self.qdrant_collection_name:
            raise ValueError("QDRANT_COLLECTION_NAME must not be empty.")
        if self.qdrant_timeout_seconds <= 0:
            raise ValueError(
                "QDRANT_TIMEOUT_SECONDS must be greater than 0."
            )


settings = Settings()
