import threading
import time

import numpy as np
import pytest

from kavach import config
from kavach.brain import ingest, llm, watcher
from kavach.models import IngestResult
from kavach.trust import audit


def _wait_for(cond, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        time.sleep(0.05)
    return False


class Recorder:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def ingest(self, path):
        with self.lock:
            self.calls.append(("ingest", path.name))
        return IngestResult(path=path.name, doc_id="d_x", source="note", signature_status="unsigned")

    def remove(self, path):
        with self.lock:
            self.calls.append(("remove", path.name))

    def snapshot(self):
        with self.lock:
            return list(self.calls)


@pytest.fixture
def vault(fresh_db, tmp_path, monkeypatch):
    root = tmp_path / "vault"
    monkeypatch.setattr(config, "VAULT_DIR", root)
    monkeypatch.setattr(config, "WATCH_DEBOUNCE_S", 0.3)
    return root


@pytest.fixture
def recorded(vault, monkeypatch):
    rec = Recorder()
    monkeypatch.setattr(ingest, "ingest_file", rec.ingest)
    monkeypatch.setattr(ingest, "remove_file", rec.remove)
    obs = watcher.start(vault)
    yield rec
    obs.stop()
    obs.join(timeout=5)


# --- filtering -----------------------------------------------------------------------------------------


@pytest.mark.parametrize("rel, watched", [
    ("pdfs/a.pdf", True), ("notes/a.md", True), ("chats/a.txt", True), ("notes/A.MD", True),
    ("notes/a.pdf", False), ("pdfs/a.md", False), ("notes/sub/a.md", False), ("a.md", False),
    ("notes/a.md.tmp", False), ("pdfs/a.pdf.crdownload", False), ("pdfs/a.tmp", False),
    ("notes/~$a.md", False), ("notes/.a.md", False), ("notes/a.md.swp", False), (".uploads/x.part", False),
])
def test_is_watched(tmp_path, rel, watched):
    assert watcher.is_watched(tmp_path / rel, tmp_path) is watched


# --- lock retry ----------------------------------------------------------------------------------------


def test_permission_error_is_retried_with_backoff(monkeypatch, tmp_path):
    attempts, sleeps = [], []

    def flaky(path):
        attempts.append(path)
        if len(attempts) <= 3:
            raise PermissionError("file is being copied")
        return IngestResult(path="notes/a.md", doc_id="d_1", source="note", signature_status="unsigned")

    monkeypatch.setattr(ingest, "ingest_file", flaky)
    res = watcher.ingest_with_retry(tmp_path / "a.md", sleep=sleeps.append)
    assert res.doc_id == "d_1" and len(attempts) == 4
    assert sleeps == [0.25, 0.5, 1.0]


def test_permission_error_gives_up_after_limit(monkeypatch, tmp_path):
    sleeps = []

    def locked(path):
        raise PermissionError("still locked")

    monkeypatch.setattr(ingest, "ingest_file", locked)
    with pytest.raises(PermissionError):
        watcher.ingest_with_retry(tmp_path / "a.md", sleep=sleeps.append)
    assert sum(sleeps) == pytest.approx(watcher.LOCK_RETRY_S)
    assert sleeps[:4] == [0.25, 0.5, 1.0, 2.0] and max(sleeps) == 2.0


def test_other_errors_are_not_retried(monkeypatch, tmp_path):
    calls = []

    def broken(path):
        calls.append(path)
        raise ValueError("unsupported")

    monkeypatch.setattr(ingest, "ingest_file", broken)
    with pytest.raises(ValueError):
        watcher.ingest_with_retry(tmp_path / "a.md", sleep=lambda s: None)
    assert len(calls) == 1


# --- live observer -------------------------------------------------------------------------------------


def test_start_creates_watched_dirs(recorded, vault):
    assert all((vault / sub).is_dir() for sub in ("pdfs", "notes", "chats"))


def test_new_file_is_ingested_once_after_debounce(recorded, vault):
    note = vault / "notes" / "rent.md"
    for i in range(5):  # a burst of writes collapses into one ingest
        note.write_text(f"Rent is ₹15,000. v{i}", encoding="utf-8")
        time.sleep(0.05)
    assert _wait_for(lambda: recorded.snapshot())
    time.sleep(0.6)
    assert recorded.snapshot() == [("ingest", "rent.md")]


def test_deleted_file_is_removed(recorded, vault):
    note = vault / "notes" / "rent.md"
    note.write_text("Rent", encoding="utf-8")
    assert _wait_for(lambda: ("ingest", "rent.md") in recorded.snapshot())
    note.unlink()
    assert _wait_for(lambda: ("remove", "rent.md") in recorded.snapshot())


def test_move_removes_old_name_and_ingests_new(recorded, vault):
    old = vault / "notes" / "draft.md"
    old.write_text("Rent", encoding="utf-8")
    assert _wait_for(lambda: ("ingest", "draft.md") in recorded.snapshot())
    old.rename(vault / "notes" / "rent.md")
    assert _wait_for(lambda: {("remove", "draft.md"), ("ingest", "rent.md")} <= set(recorded.snapshot()))


def test_temp_and_partial_files_are_ignored(recorded, vault):
    for rel in ("pdfs/a.pdf.crdownload", "pdfs/b.tmp", "notes/~$c.md", "notes/.d.md", "notes/e.pdf"):
        (vault / rel).write_bytes(b"x")
    (vault / "notes" / "real.md").write_text("x", encoding="utf-8")
    assert _wait_for(lambda: recorded.snapshot())
    time.sleep(0.6)
    assert recorded.snapshot() == [("ingest", "real.md")]


def test_catch_up_on_start(vault, fresh_db, monkeypatch):
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "existing.md").write_text("x", encoding="utf-8")
    fresh_db.insert("documents", {"doc_id": "d_gone", "path": "notes/gone.md", "source": "note",
                                  "signature_status": "unsigned", "ingested_at": "2026-09-26T10:00:00Z"})
    rec = Recorder()
    monkeypatch.setattr(ingest, "ingest_file", rec.ingest)
    monkeypatch.setattr(ingest, "remove_file", rec.remove)
    obs = watcher.start(vault)
    try:
        assert _wait_for(lambda: len(rec.snapshot()) == 2)
        assert set(rec.snapshot()) == {("ingest", "existing.md"), ("remove", "gone.md")}
    finally:
        obs.stop()
        obs.join(timeout=5)


def test_a_failing_file_does_not_stop_the_watcher(recorded, vault, monkeypatch):
    def boom(path):
        if path.name == "bad.md":
            raise RuntimeError("boom")
        return recorded.ingest(path)

    monkeypatch.setattr(ingest, "ingest_file", boom)
    (vault / "notes" / "bad.md").write_text("x", encoding="utf-8")
    time.sleep(0.5)
    (vault / "notes" / "good.md").write_text("x", encoding="utf-8")
    assert _wait_for(lambda: ("ingest", "good.md") in recorded.snapshot())


def test_end_to_end_drop_a_note(vault, fresh_db, monkeypatch):
    """Real ingest behind the real watcher: a dropped note becomes a document with chunks."""
    monkeypatch.setattr(llm, "embed", lambda texts: np.ones((len(texts), config.EMBED_DIM), dtype=np.float32))
    events = []
    monkeypatch.setattr(audit, "log", lambda event, ref_id, detail: events.append(event) or 1)
    obs = watcher.start(vault)
    try:
        (vault / "notes" / "rent.md").write_text("Rent is ₹15,000, due on the 5th.", encoding="utf-8")
        assert _wait_for(lambda: fresh_db.list_documents())
        doc = fresh_db.list_documents()[0]
        assert doc.path == "notes/rent.md" and events == ["ingested"]
        (vault / "notes" / "rent.md").unlink()
        assert _wait_for(lambda: not fresh_db.list_documents())
        assert _wait_for(lambda: events == ["ingested", "document_removed"])
    finally:
        obs.stop()
        obs.join(timeout=5)
