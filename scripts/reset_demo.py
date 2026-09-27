"""Restore a clean demo state (BUILD_PLAN.md section 5).

Usage:
    python scripts/reset_demo.py            wipe runtime state, issue signed PDFs + credential batches, copy demo_data
                                            into vault, pre-ingest, back up kavach.db
    python scripts/reset_demo.py --restore  put the kavach.db backup back (seconds)

Issuer keys (keys/issuers/) and the owner token are never touched; the wallet's one-time holder keys live in the
database with their credentials, so --restore brings back the exact wallet. The issuers' trust list is also written
to the requester's data dir (REQUESTER_DATA_DIR, default requester/data/) for a single-laptop demo; for two laptops
copy keys/issuers/trusted_issuers.json to requester/data/ on the requester laptop.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kavach import config, db  # noqa: E402

VAULT_SUBDIRS = ("pdfs", "notes", "chats")
HELD_BACK = {"inbox_note.md", "bank_statement_TAMPERED.pdf"}  # dropped in live during the demo
BATCH_SIZE = 20


def backup_path() -> Path:
    return config.DB_PATH.with_name(config.DB_PATH.name + ".bak")


def requester_data_dir() -> Path:
    return Path(os.environ.get("REQUESTER_DATA_DIR", config.ROOT / "requester" / "data"))


def wipe_runtime() -> None:
    for sidecar in ("", "-wal", "-shm"):
        config.DB_PATH.with_name(config.DB_PATH.name + sidecar).unlink(missing_ok=True)
    for d in (config.VAULT_DIR, config.OUTBOX_DIR, config.KEYS_DIR / "wallet"):
        shutil.rmtree(d, ignore_errors=True)
    for sub in VAULT_SUBDIRS:
        (config.VAULT_DIR / sub).mkdir(parents=True, exist_ok=True)
    config.OUTBOX_DIR.mkdir(parents=True, exist_ok=True)


def copy_demo_data() -> int:
    """Copy demo_data/{notes,chats} and demo_data/generated/*.pdf into vault/. Returns files copied."""
    copied = 0
    for sub in ("notes", "chats"):
        src = config.DEMO_DATA_DIR / sub
        for f in sorted(src.glob("*")) if src.is_dir() else []:
            if f.is_file() and f.name not in HELD_BACK:
                shutil.copy2(f, config.VAULT_DIR / sub / f.name)
                copied += 1
    generated = config.DEMO_DATA_DIR / "generated"
    for f in sorted(generated.glob("*.pdf")) if generated.is_dir() else []:
        if f.name not in HELD_BACK:
            shutil.copy2(f, config.VAULT_DIR / "pdfs" / f.name)
            copied += 1
    return copied


def run_issuers() -> None:
    """Issuer keys, signed + tampered PDFs into demo_data/generated/, a batch of each credential into the wallet."""
    from kavach.mock_issuers import issue, make_keys
    from kavach.trust import wallet

    make_keys.ensure_keys()
    pdfs = issue.make_pdfs(config.DEMO_DATA_DIR / "generated")
    print(f"  issuers: {len(pdfs)} PDFs in {config.DEMO_DATA_DIR / 'generated'}")
    for ctype in issue.CREDENTIAL_TYPES:
        pubkeys = wallet.export_holder_pubkeys(BATCH_SIZE)
        batch = issue.issue_batch(ctype, json.loads(pubkeys.read_text(encoding="utf-8")))
        batch_path = pubkeys.with_name(f"batch_{ctype}.json")
        batch_path.write_text(json.dumps(batch), encoding="utf-8")
        print(f"  wallet: {wallet.import_batch(batch_path)} {ctype} copies from {batch['iss']}")
        pubkeys.unlink()
        batch_path.unlink()
    trust = make_keys.export_trust_list(requester_data_dir() / "trusted_issuers.json")
    print(f"  requester trust list: {trust}")


def pre_ingest() -> None:
    """Ingest every vault file so the demo starts warm (works offline; embeddings backfill when Ollama is up)."""
    from kavach.brain import ingest

    done = 0
    for sub in VAULT_SUBDIRS:
        for f in sorted((config.VAULT_DIR / sub).glob("*")):
            if f.is_file() and f.suffix.lower() in (".pdf", ".md", ".txt"):
                try:
                    r = ingest.ingest_file(f)
                    done += 1
                    print(f"  ingest: {r.path} ({r.signature_status}, {r.chunks_added} chunks)")
                except Exception as exc:  # noqa: BLE001 - one bad file must not stop the reset
                    print(f"  ingest: {f.name} failed: {type(exc).__name__}: {exc}")
    print(f"  ingest: {done} files")


def reset() -> None:
    print("Resetting demo state")
    wipe_runtime()
    db.init_db()
    run_issuers()
    print(f"  copied {copy_demo_data()} files into {config.VAULT_DIR}")
    pre_ingest()
    with db.connect() as conn:  # fold the WAL into the main file before copying it
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy2(config.DB_PATH, backup_path())
    print(f"  backup: {backup_path()}")


def restore() -> None:
    if not backup_path().exists():
        raise SystemExit(f"No backup at {backup_path()}; run without --restore first.")
    for sidecar in ("-wal", "-shm"):
        config.DB_PATH.with_name(config.DB_PATH.name + sidecar).unlink(missing_ok=True)
    shutil.copy2(backup_path(), config.DB_PATH)
    shutil.rmtree(config.OUTBOX_DIR, ignore_errors=True)
    config.OUTBOX_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Restored {config.DB_PATH} from backup")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--restore", action="store_true", help="restore kavach.db from the last backup")
    args = parser.parse_args(argv)
    restore() if args.restore else reset()


if __name__ == "__main__":
    main()
