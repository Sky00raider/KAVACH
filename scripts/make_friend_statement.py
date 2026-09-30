"""A genuine, bank-signed statement in someone else's name, for the holder-check moment (CONTRACT §6.6).

Usage:
    python scripts/make_friend_statement.py                 writes ~/Desktop/demo_drop/friend_bank_statement.pdf
    python scripts/make_friend_statement.py --out DIR

The mock bank signs it with this laptop's issuer key (keys/issuers/), so it verifies as `issuer_signed`; the holder
check then finds another name and date of birth and marks it `mismatch`. Run it again after the issuer keys change.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kavach.mock_issuers import issue  # noqa: E402

FRIEND = {"name": "Rohan Mehta", "date_of_birth": "2001-11-02", "monthly_income": 95000, "landlord": "Suresh Rao",
          "employer": "Orbit Labs Pvt Ltd", "account": "XXXXXXXX9034"}
FILE_NAME = "friend_bank_statement.pdf"


def make(out_dir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        issue.make_pdfs(Path(tmp), {**issue.load_profile(), **FRIEND})
        out_dir.mkdir(parents=True, exist_ok=True)
        dest = out_dir / FILE_NAME
        shutil.copy(Path(tmp) / "bank_statement_signed.pdf", dest)
    return dest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path.home() / "Desktop" / "demo_drop")
    args = parser.parse_args(argv)
    print(f"wrote {make(args.out)}")


if __name__ == "__main__":
    main()
