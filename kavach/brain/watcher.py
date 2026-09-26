"""watchdog on vault/{pdfs,notes,chats}; debounced create/modify -> ingest_file, delete -> remove_file."""

from __future__ import annotations

from pathlib import Path

from watchdog.observers import Observer


def start(vault_dir: Path) -> Observer:
    """Stub: starts an observer with no handlers. BRAIN step 2 schedules the real handler."""
    observer = Observer()
    observer.daemon = True
    observer.start()
    return observer
