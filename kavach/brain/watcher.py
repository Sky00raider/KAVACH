"""watchdog on vault/{pdfs,notes,chats}; debounced create/modify -> ingest_file, delete -> remove_file.

Events are collected per path and acted on once the path has been quiet for WATCH_DEBOUNCE_S. At that point the
file's existence decides: present -> ingest, gone -> remove. One worker thread runs every action, so ingests never
overlap. On start, files already in the vault are queued and documents whose files vanished are removed.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from collections.abc import Callable
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from kavach import config, db
from kavach.brain import ingest
from kavach.models import IngestResult

log = logging.getLogger(__name__)

WATCHED = {"pdfs": ".pdf", "notes": ".md", "chats": ".txt"}
_IGNORED_SUFFIXES = (".tmp", ".crdownload", ".part", ".swp", ".swx")
LOCK_RETRY_S = 10.0  # how long a Windows copy lock (PermissionError) is retried before giving up


def is_watched(path: Path, vault_dir: Path) -> bool:
    """A direct child of vault/<sub> with that sub's suffix; temp, lock, partial-download and hidden files are not."""
    name = path.name
    if name.startswith((".", "~")) or name.lower().endswith(_IGNORED_SUFFIXES):
        return False
    suffix = WATCHED.get(path.parent.name)
    return suffix == path.suffix.lower() and path.parent.parent.resolve() == Path(vault_dir).resolve()


def ingest_with_retry(path: Path, limit_s: float | None = None,
                      sleep: Callable[[float], None] = time.sleep) -> IngestResult:
    """ingest_file, retrying PermissionError with backoff (0.25 s doubling to 2 s) for up to `limit_s` seconds."""
    limit = LOCK_RETRY_S if limit_s is None else limit_s
    waited, delay = 0.0, 0.25
    while True:
        try:
            return ingest.ingest_file(path)
        except PermissionError:
            if waited >= limit:
                raise
            pause = min(delay, limit - waited)
            sleep(pause)
            waited += pause
            delay = min(delay * 2, 2.0)


def _process(path: Path) -> None:
    try:
        if path.is_file():
            ingest_with_retry(path)
        else:
            ingest.remove_file(path)
    except Exception:
        log.exception("watcher could not process %s", path)


class _Debouncer:
    """Runs `action(path)` once a path has had no new events for `delay` seconds, on one worker thread."""

    def __init__(self, delay: float, action: Callable[[Path], None]):
        self._delay = delay
        self._action = action
        self._due: dict[Path, float] = {}
        self._cond = threading.Condition()
        self._stopped = False
        self._thread = threading.Thread(target=self._loop, name="kavach-watcher", daemon=True)
        self._thread.start()

    def push(self, path: Path) -> None:
        with self._cond:
            self._due[path] = time.monotonic() + self._delay
            self._cond.notify()

    def stop(self) -> None:
        with self._cond:
            self._stopped = True
            self._cond.notify()

    def _next(self) -> Path | None:
        with self._cond:
            while not self._stopped:
                if not self._due:
                    self._cond.wait()
                    continue
                path, due = min(self._due.items(), key=lambda item: item[1])
                wait = due - time.monotonic()
                if wait <= 0:
                    del self._due[path]
                    return path
                self._cond.wait(wait)
            return None

    def _loop(self) -> None:
        while (path := self._next()) is not None:
            self._action(path)


class _Handler(FileSystemEventHandler):
    def __init__(self, vault_dir: Path, debouncer: _Debouncer):
        self._vault_dir = vault_dir
        self._debouncer = debouncer

    def _push(self, raw: str | bytes) -> None:
        path = Path(os.fsdecode(raw))
        if is_watched(path, self._vault_dir):
            self._debouncer.push(path)

    def on_any_event(self, event: FileSystemEvent) -> None:
        if event.is_directory or event.event_type not in ("created", "modified", "deleted", "moved"):
            return
        self._push(event.src_path)
        if event.event_type == "moved":
            self._push(event.dest_path)


class VaultObserver(Observer):  # type: ignore[misc, valid-type]
    """The platform observer; stop() also stops the debounce worker."""

    def __init__(self, debouncer: _Debouncer):
        super().__init__()
        self.debouncer = debouncer

    def stop(self) -> None:
        self.debouncer.stop()
        super().stop()


def _catch_up(vault_dir: Path, debouncer: _Debouncer) -> None:
    """Queue files added while KAVACH was off and documents whose files were deleted meanwhile."""
    for sub in WATCHED:
        folder = vault_dir / sub
        for path in sorted(folder.iterdir()) if folder.is_dir() else ():
            if path.is_file() and is_watched(path, vault_dir):
                debouncer.push(path)
    try:
        documents = db.list_documents()
    except sqlite3.Error as exc:
        log.warning("watcher catch-up skipped removals: %s", exc)
        return
    for doc in documents:
        path = vault_dir / doc.path
        if not path.exists():
            debouncer.push(path)


def start(vault_dir: Path) -> Observer:
    """Watch vault/{pdfs,notes,chats} (created if missing) and catch up on changes made while stopped."""
    vault_dir = Path(vault_dir)
    debouncer = _Debouncer(config.WATCH_DEBOUNCE_S, _process)
    observer = VaultObserver(debouncer)
    handler = _Handler(vault_dir, debouncer)
    for sub in WATCHED:
        folder = vault_dir / sub
        folder.mkdir(parents=True, exist_ok=True)
        observer.schedule(handler, str(folder), recursive=False)
    observer.daemon = True
    observer.start()
    _catch_up(vault_dir, debouncer)
    return observer
