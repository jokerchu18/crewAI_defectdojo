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


settings = Settings()
