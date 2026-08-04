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
        self.model = os.getenv("model")
        self.api_key = os.getenv("api_key")
        self.base_url = os.getenv("base_url")
        self.defectdojo_base_url = os.getenv("DEFECTDOJO_BASE_URL", "http://localhost:8080")
        self.defectdojo_api_key = os.getenv("DEFECTDOJO_API_KEY", "")
        self.defectdojo_engagement_id = int(os.getenv("DEFECTDOJO_ENGAGEMENT_ID", "1"))
        self.defectdojo_tool_max_concurrency = int(
            os.getenv("DEFECTDOJO_TOOL_MAX_CONCURRENCY", "8")
        )

        # ── Tool timeout & resilience ───────────────────────────────
        self.defectdojo_tool_connect_timeout = float(
            os.getenv("DEFECTDOJO_TOOL_CONNECT_TIMEOUT", "10")
        )
        self.defectdojo_tool_read_timeout = float(
            os.getenv("DEFECTDOJO_TOOL_READ_TIMEOUT", "60")
        )
        self.defectdojo_tool_write_timeout = float(
            os.getenv("DEFECTDOJO_TOOL_WRITE_TIMEOUT", "60")
        )
        self.defectdojo_tool_import_timeout = float(
            os.getenv("DEFECTDOJO_TOOL_IMPORT_TIMEOUT", "300")
        )
        self.defectdojo_tool_max_retries = int(
            os.getenv("DEFECTDOJO_TOOL_MAX_RETRIES", "2")
        )
        self.defectdojo_tool_retry_backoff_base = float(
            os.getenv("DEFECTDOJO_TOOL_RETRY_BACKOFF_BASE", "1.0")
        )
        self.defectdojo_tool_retry_backoff_max = float(
            os.getenv("DEFECTDOJO_TOOL_RETRY_BACKOFF_MAX", "30.0")
        )
        self.defectdojo_tool_circuit_breaker_enabled = get_bool_env(
            "DEFECTDOJO_TOOL_CIRCUIT_BREAKER_ENABLED", True
        )
        self.defectdojo_tool_circuit_breaker_threshold = int(
            os.getenv("DEFECTDOJO_TOOL_CIRCUIT_BREAKER_THRESHOLD", "3")
        )
        self.defectdojo_tool_circuit_breaker_recovery = float(
            os.getenv("DEFECTDOJO_TOOL_CIRCUIT_BREAKER_RECOVERY", "60.0")
        )

        self.agent_router_timeout = float(
            os.getenv("AGENT_ROUTER_TIMEOUT", "60")
        )
        self.agent_default_timeout = float(
            os.getenv("AGENT_DEFAULT_TIMEOUT", "300")
        )
        self.agent_import_timeout = float(
            os.getenv("AGENT_IMPORT_TIMEOUT", "600")
        )
        self.agent_risk_acceptance_timeout = float(
            os.getenv("AGENT_RISK_ACCEPTANCE_TIMEOUT", "300")
        )

        self.default_scan_type = os.getenv("DEFAULT_SCAN_TYPE", "SARIF")
        self.default_scan_file_path = os.getenv(
            "DEFAULT_SCAN_FILE_PATH",
            r"D:\github\crewAI_defectdojo\sample_reports\sample_multi.sarif",
        )

        self.log_level = os.getenv("LOG_LEVEL", "WARNING").upper()
        self.business_log_level = os.getenv(
            "BUSINESS_LOG_LEVEL",
            self.log_level,
        ).upper()
        self.system_log_level = os.getenv(
            "SYSTEM_LOG_LEVEL",
            self.log_level,
        ).upper()
        self.console_system_log_level = os.getenv(
            "CONSOLE_SYSTEM_LOG_LEVEL",
            "WARNING",
        ).upper()
        self.log_dir = Path(
            os.getenv("LOG_DIR", str(BASE_DIR / "data" / "logs"))
        ).expanduser()
        if not self.log_dir.is_absolute():
            self.log_dir = (BASE_DIR / self.log_dir).resolve()
        self.log_max_bytes = int(
            os.getenv("LOG_MAX_BYTES", str(10 * 1024 * 1024))
        )
        self.log_backup_count = int(os.getenv("LOG_BACKUP_COUNT", "5"))
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
        default_embedding_dimensions = {
            "fastembed": 512,
            "tei": 1024,
            "openai": 1536,
        }.get(self.embedding_provider, 1024)
        self.embedding_dimensions = int(
            os.getenv(
                "EMBEDDING_DIMENSIONS",
                str(default_embedding_dimensions),
            )
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
            os.getenv("QDRANT_TIMEOUT_SECONDS", "10")
        )
        self.qdrant_tool_connect_timeout = float(
            os.getenv("QDRANT_TOOL_CONNECT_TIMEOUT", "5")
        )
        self.qdrant_prefer_grpc = get_bool_env(
            "QDRANT_PREFER_GRPC",
            False,
        )
        self.knowledge_min_similarity = float(
            os.getenv("KNOWLEDGE_MIN_SIMILARITY", "0.35")
        )
        self.hybrid_search_enabled = get_bool_env(
            "HYBRID_SEARCH_ENABLED",
            False,
        )
        self.hybrid_search_source_types_raw = os.getenv(
            "HYBRID_SEARCH_SOURCE_TYPES",
            "library",
        )
        self.router_fallback_confidence_threshold = float(
            os.getenv("ROUTER_FALLBACK_CONFIDENCE_THRESHOLD", "0.7")
        )
        self.router_fallback_top_k = int(
            os.getenv("ROUTER_FALLBACK_TOP_K", "4")
        )
        self.router_fallback_min_similarity = float(
            os.getenv("ROUTER_FALLBACK_MIN_SIMILARITY", "0.75")
        )
        self.router_fallback_min_consensus = int(
            os.getenv("ROUTER_FALLBACK_MIN_CONSENSUS", "2")
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
        self.context_history_token_budget = int(
            os.getenv("CONTEXT_HISTORY_TOKEN_BUDGET", "3000")
        )
        self.context_summary_token_budget = int(
            os.getenv("CONTEXT_SUMMARY_TOKEN_BUDGET", "400")
        )
        self.context_summary_input_token_budget = int(
            os.getenv("CONTEXT_SUMMARY_INPUT_TOKEN_BUDGET", "3000")
        )
        self.context_summary_enabled = get_bool_env(
            "CONTEXT_SUMMARY_ENABLED",
            True,
        )
        self.context_max_chars = int(
            os.getenv("CONTEXT_MAX_CHARS", "12000")
        )
        self.context_workflow_max_steps = int(
            os.getenv("CONTEXT_WORKFLOW_MAX_STEPS", "8")
        )
        self.context_agent_output_max_chars = int(
            os.getenv("CONTEXT_AGENT_OUTPUT_MAX_CHARS", "4000")
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
        if self.defectdojo_tool_connect_timeout <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_CONNECT_TIMEOUT must be greater than 0."
            )
        if self.defectdojo_tool_read_timeout <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_READ_TIMEOUT must be greater than 0."
            )
        if self.defectdojo_tool_write_timeout <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_WRITE_TIMEOUT must be greater than 0."
            )
        if self.defectdojo_tool_import_timeout <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_IMPORT_TIMEOUT must be greater than 0."
            )
        if self.defectdojo_tool_max_retries < 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_MAX_RETRIES must be >= 0."
            )
        if self.defectdojo_tool_retry_backoff_base <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_RETRY_BACKOFF_BASE must be greater than 0."
            )
        if self.defectdojo_tool_retry_backoff_max <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_RETRY_BACKOFF_MAX must be greater than 0."
            )
        if self.defectdojo_tool_circuit_breaker_threshold <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_CIRCUIT_BREAKER_THRESHOLD must be greater than 0."
            )
        if self.defectdojo_tool_circuit_breaker_recovery <= 0:
            raise ValueError(
                "DEFECTDOJO_TOOL_CIRCUIT_BREAKER_RECOVERY must be greater than 0."
            )
        if self.agent_router_timeout <= 0:
            raise ValueError("AGENT_ROUTER_TIMEOUT must be greater than 0.")
        if self.agent_default_timeout <= 0:
            raise ValueError("AGENT_DEFAULT_TIMEOUT must be greater than 0.")
        if self.agent_import_timeout <= 0:
            raise ValueError("AGENT_IMPORT_TIMEOUT must be greater than 0.")
        if self.agent_risk_acceptance_timeout <= 0:
            raise ValueError(
                "AGENT_RISK_ACCEPTANCE_TIMEOUT must be greater than 0."
            )
        if self.session_history_max_messages <= 0:
            raise ValueError(
                "SESSION_HISTORY_MAX_MESSAGES must be greater than 0."
            )
        if self.context_history_token_budget <= 0:
            raise ValueError(
                "CONTEXT_HISTORY_TOKEN_BUDGET must be greater than 0."
            )
        if self.context_summary_token_budget <= 0:
            raise ValueError(
                "CONTEXT_SUMMARY_TOKEN_BUDGET must be greater than 0."
            )
        if self.context_summary_input_token_budget <= 0:
            raise ValueError(
                "CONTEXT_SUMMARY_INPUT_TOKEN_BUDGET must be greater than 0."
            )
        if self.context_max_chars <= 0:
            raise ValueError("CONTEXT_MAX_CHARS must be greater than 0.")
        if self.context_workflow_max_steps <= 0:
            raise ValueError(
                "CONTEXT_WORKFLOW_MAX_STEPS must be greater than 0."
            )
        if self.context_agent_output_max_chars <= 0:
            raise ValueError(
                "CONTEXT_AGENT_OUTPUT_MAX_CHARS must be greater than 0."
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
        if self.log_max_bytes <= 0:
            raise ValueError("LOG_MAX_BYTES must be greater than 0.")
        if self.log_backup_count < 0:
            raise ValueError("LOG_BACKUP_COUNT must not be negative.")
        if self.knowledge_top_k <= 0:
            raise ValueError("KNOWLEDGE_TOP_K must be greater than 0.")
        if self.knowledge_max_chars <= 0:
            raise ValueError("KNOWLEDGE_MAX_CHARS must be greater than 0.")
        self._hybrid_search_source_types: frozenset[str] | None = None
        if self.hybrid_search_enabled:
            if self.embedding_provider not in {"tei", "openai"}:
                raise ValueError(
                    "HYBRID_SEARCH_ENABLED requires EMBEDDING_PROVIDER=tei "
                    "(or openai with a BGE-M3 compatible endpoint). "
                    f"Current provider: {self.embedding_provider}."
                )
            raw = self.hybrid_search_source_types_raw.strip()
            source_types = {
                value.strip()
                for value in raw.split(",")
                if value.strip()
            }
            valid = {"library", "audit", "triage", "remediation"}
            invalid = source_types - valid
            if invalid:
                raise ValueError(
                    "HYBRID_SEARCH_SOURCE_TYPES contains unknown types: "
                    f"{', '.join(sorted(invalid))}. "
                    f"Valid: {', '.join(sorted(valid))}."
                )
            self._hybrid_search_source_types = frozenset(source_types)
        if self.embedding_provider not in {"fastembed", "openai", "tei"}:
            raise ValueError(
                "EMBEDDING_PROVIDER must be 'fastembed', 'tei', or 'openai'."
            )
        if self.embedding_dimensions <= 0:
            raise ValueError("EMBEDDING_DIMENSIONS must be greater than 0.")
        if not 0 <= self.knowledge_min_similarity <= 1:
            raise ValueError(
                "KNOWLEDGE_MIN_SIMILARITY must be between 0 and 1."
            )
        if not 0 <= self.router_fallback_confidence_threshold <= 1:
            raise ValueError(
                "ROUTER_FALLBACK_CONFIDENCE_THRESHOLD must be between 0 and 1."
            )
        if self.router_fallback_top_k <= 0:
            raise ValueError("ROUTER_FALLBACK_TOP_K must be greater than 0.")
        if not 0 <= self.router_fallback_min_similarity <= 1:
            raise ValueError(
                "ROUTER_FALLBACK_MIN_SIMILARITY must be between 0 and 1."
            )
        if self.router_fallback_min_consensus <= 0:
            raise ValueError(
                "ROUTER_FALLBACK_MIN_CONSENSUS must be greater than 0."
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


def hybrid_search_source_types() -> frozenset[str]:
    """Source types that should use hybrid (dense + sparse) search."""
    return settings._hybrid_search_source_types or frozenset()
