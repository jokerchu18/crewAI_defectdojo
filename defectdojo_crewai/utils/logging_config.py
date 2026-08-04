"""Central logging configuration for business and system events."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from defectdojo_crewai.config.settings import settings


_BUSINESS_PREFIXES = ("defectdojo_crewai", "defectdojo-mcp")
_CONFIGURED = False


class _BusinessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name.startswith(_BUSINESS_PREFIXES)


class _SystemLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return not record.name.startswith(_BUSINESS_PREFIXES)


def configure_logging(*, force: bool = False) -> None:
    """Split application and dependency logs into separate rotating files."""
    global _CONFIGURED

    if _CONFIGURED and not force:
        return

    settings.log_dir.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    for handler in root.handlers:
        handler.close()
    root.handlers.clear()
    root.setLevel(logging.DEBUG)

    business_filter = _BusinessLogFilter()
    system_filter = _SystemLogFilter()
    file_formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    root.addHandler(
        _rotating_handler(
            settings.log_dir / "business.log",
            settings.business_log_level,
            business_filter,
            file_formatter,
        )
    )
    root.addHandler(
        _rotating_handler(
            settings.log_dir / "system.log",
            settings.system_log_level,
            system_filter,
            file_formatter,
        )
    )

    business_console = logging.StreamHandler()
    business_console.setLevel(settings.business_log_level)
    business_console.addFilter(business_filter)
    business_console.setFormatter(
        logging.Formatter(
            "%(asctime)s BUSINESS %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(business_console)

    system_console = logging.StreamHandler()
    system_console.setLevel(settings.console_system_log_level)
    system_console.addFilter(system_filter)
    system_console.setFormatter(
        logging.Formatter(
            "%(asctime)s SYSTEM %(levelname)s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(system_console)

    _CONFIGURED = True


def _rotating_handler(
    path: Path,
    level: str,
    log_filter: logging.Filter,
    formatter: logging.Formatter,
) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path,
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    handler.setLevel(level)
    handler.addFilter(log_filter)
    handler.setFormatter(formatter)
    return handler
