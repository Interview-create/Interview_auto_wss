import logging
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_LOGGER: logging.Logger | None = None


def _build_log_path(config: dict[str, Any], run_ts: str) -> Path:
    log_dir = Path(config.get("dir", "event/logs"))
    base_name = config.get("base_name", "app")
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"{base_name}_{run_ts}.log"


def _get_or_create_default_logger() -> logging.Logger:
    global _LOGGER
    if _LOGGER is not None:
        return _LOGGER
    logger = logging.getLogger("app")
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.NullHandler())
    _LOGGER = logger
    return logger


def init_logger(config: dict[str, Any], logger_name: str = "app") -> None:
    global _LOGGER
    logger = logging.getLogger(logger_name)
    logger.propagate = False

    if logger.handlers:
        _LOGGER = logger
        return

    if not config.get("enabled", True):
        logger.addHandler(logging.NullHandler())
        _LOGGER = logger
        return

    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = _build_log_path(config, run_ts)

    level_name = str(config.get("level", "INFO")).upper()
    level = getattr(logging, level_name, logging.INFO)
    logger.setLevel(level)

    handler = RotatingFileHandler(
        log_path,
        maxBytes=int(config.get("max_bytes", 10 * 1024 * 1024)),
        backupCount=int(config.get("backup_count", 5)),
        encoding="utf-8",
    )
    fmt = config.get("format", "%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    datefmt = config.get("datefmt", "%Y-%m-%d %H:%M:%S")
    handler.setFormatter(logging.Formatter(fmt=fmt, datefmt=datefmt))
    logger.addHandler(handler)
    _LOGGER = logger


def log_debug(message: str, *args: Any) -> None:
    _get_or_create_default_logger().debug(message, *args)


def log_info(message: str, *args: Any) -> None:
    _get_or_create_default_logger().info(message, *args)


def log_warning(message: str, *args: Any) -> None:
    _get_or_create_default_logger().warning(message, *args)


def log_error(message: str, *args: Any) -> None:
    _get_or_create_default_logger().error(message, *args)


def log_exception(message: str, *args: Any) -> None:
    _get_or_create_default_logger().exception(message, *args)
