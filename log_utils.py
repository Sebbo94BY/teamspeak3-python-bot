"""Shared logging configuration helpers."""

from logging.handlers import TimedRotatingFileHandler

LOG_RETENTION_DAYS = 14


def create_log_handler(filename):
    """Create a UTF-8 file handler that rotates at midnight and retains 14 files."""
    return TimedRotatingFileHandler(
        filename,
        when="midnight",
        backupCount=LOG_RETENTION_DAYS,
        encoding="utf-8",
        delay=True,
    )
