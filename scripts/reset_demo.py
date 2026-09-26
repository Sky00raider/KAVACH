"""Restore a clean demo state (BUILD_PLAN.md section 5). Skeleton: file steps work; issuing and ingest are TODO.

Usage:
    python scripts/reset_demo.py            wipe runtime state, copy demo_data into vault, issue, ingest, back up db
    python scripts/reset_demo.py --restore  put the kavach.db backup back (seconds)

keys/ (owner token, issuer and wallet keys) is never touched.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kavach import config, db  # noqa: E402

VAULT_SUBDIRS = ("pdfs", "notes", "chats")
HELD_BACK = {"inbox_note.md"}  # dropped in live during the demo


def backup_path() -> Path:
    return config.DB_PATH.with_name(config.DB_PATH.name + ".bak")


def wipe_runtime() -> None:
    for sidecar in ("", "-wal", "-shm"):
        config.DB_PATH.with_name(config.DB_PATH.name + sidecar).unlink(missing_ok=True)
    for d in (config.VAULT_DIR, config.OUTBOX_DIR):
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
        shutil.copy2(f, config.VAULT_DIR / "pdfs" / f.name)
        copied += 1
    return copied


def run_issuers() -> None:
    # TODO(TRUST): make_keys, signed + tampered PDFs into demo_data/generated/, credential batches into the wallet.
    print("  issuers: skipped (not implemented)")


def pre_ingest() -> None:
    # TODO(BRAIN): ingest every vault file so the demo starts warm.
    print("  ingest: skipped (not implemented)")


def reset() -> None:
    print("Resetting demo state")
    wipe_runtime()
    db.init_db()
    run_issuers()
    print(f"  copied {copy_demo_data()} files into {config.VAULT_DIR}")
    pre_ingest()
    shutil.copy2(config.DB_PATH, backup_path())
    print(f"  backup: {backup_path()}")


def restore() -> None:
    if not backup_path().exists():
        raise SystemExit(f"No backup at {backup_path()}; run without --restore first.")
    for sidecar in ("-wal", "-shm"):
        config.DB_PATH.with_name(config.DB_PATH.name + sidecar).unlink(missing_ok=True)
    shutil.copy2(backup_path(), config.DB_PATH)
    print(f"Restored {config.DB_PATH} from backup")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--restore", action="store_true", help="restore kavach.db from the last backup")
    args = parser.parse_args(argv)
    restore() if args.restore else reset()


if __name__ == "__main__":
    main()
