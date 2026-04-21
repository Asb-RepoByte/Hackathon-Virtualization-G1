"""
Centralized Logging Module for Infrastructure Orchestrator.

Usage:
    from logger import get_logger, get_recent_logs

    log = get_logger("vm_manager")
    log.info("VM UP requested", extra={"action": "VM_UP", "detail": "state_index=0"})

Log files are written to ./logs/actions.log with 5MB rotation (3 backups).
"""

import os
import logging
import logging.handlers
import json
from datetime import datetime, timezone

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
LOG_FILE = os.path.join(LOG_DIR, "actions.log")
MAX_BYTES = 5 * 1024 * 1024  # 5MB
BACKUP_COUNT = 3


def _ensure_log_dir():
    os.makedirs(LOG_DIR, exist_ok=True)


class ActionFormatter(logging.Formatter):
    """
    Formats log lines as:
    2026-04-20 03:45:12 | vm_manager | INFO | VM UP requested
    """
    def format(self, record):
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        source = record.name
        level = record.levelname
        msg = record.getMessage()
        return f"{ts} | {source} | {level} | {msg}"


def get_logger(name: str) -> logging.Logger:
    """
    Returns a named logger that writes to both stdout and the rotating log file.
    Safe to call multiple times with the same name (returns cached logger).
    """
    _ensure_log_dir()

    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers if called multiple times
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)
    formatter = ActionFormatter()

    # File handler with rotation
    fh = logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT
    )
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Stdout handler
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


def get_recent_logs(n: int = 50) -> list:
    """
    Returns the last `n` log lines from the active log file.
    Returns newest-first order.
    """
    _ensure_log_dir()
    if not os.path.exists(LOG_FILE):
        return []

    try:
        with open(LOG_FILE, "r") as f:
            lines = f.readlines()
        # Return last n lines, newest first
        return [line.strip() for line in lines[-n:]][::-1]
    except Exception:
        return []
