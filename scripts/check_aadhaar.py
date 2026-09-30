"""Manual check of a real Aadhaar offline e-KYC ZIP against the bundled UIDAI certificates (CONTRACT §6.6).

Usage (keep the file in private/, which git ignores):
    python scripts/check_aadhaar.py private/offlineaadhaar<...>.zip        asks for the share code (not echoed)

Verification only: nothing is stored, logged or sent anywhere. It prints whether the signature verified, the issuer,
the name's initials and the year of birth, never the name, full date of birth, digits, address or photo, so the
output is safe on screen.
"""

from __future__ import annotations

import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from kavach.brain.identity import initials  # noqa: E402
from kavach.trust import aadhaar  # noqa: E402


def check(path: Path, share_code: str) -> str:
    try:
        found = aadhaar.verify_okyc(path.read_bytes(), share_code)
    except aadhaar.AadhaarError as exc:
        return f"verified: no ({exc.reason})"
    born = found.dob[:4] if found.dob else "unknown"
    return f"verified: yes | issuer: {aadhaar.ISSUER} | name: {initials(found.name)} | born: {born}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("zip", type=Path)
    args = parser.parse_args(argv)
    print(check(args.zip, getpass.getpass("Share code: ")))


if __name__ == "__main__":
    main()
