from .history import (
    archive_conversion,
    cleanup_expired,
    delete_record,
    delete_records,
    ensure_file_dir,
    file_dir,
    get_record,
    list_records,
    record_count,
    resolve_stored,
    retention_days,
    storage_stats,
)

# Lazy aliases so ``from storage import FILE_DIR`` stays current after settings load.
def __getattr__(name: str):
    if name == "FILE_DIR":
        return file_dir()
    if name == "RETENTION_DAYS":
        return retention_days()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "FILE_DIR",
    "RETENTION_DAYS",
    "archive_conversion",
    "cleanup_expired",
    "delete_record",
    "delete_records",
    "ensure_file_dir",
    "file_dir",
    "get_record",
    "list_records",
    "record_count",
    "resolve_stored",
    "retention_days",
    "storage_stats",
]
