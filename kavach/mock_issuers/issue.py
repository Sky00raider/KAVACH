"""Issue signed PDFs and credential batches (CONTRACT §6.1, §6.5).

Usage: python -m kavach.mock_issuers.issue --type income_proof --holder-keys pubkeys.json
"""

from __future__ import annotations

import argparse

CREDENTIAL_TYPES = {"income_proof": "mock_bank", "marksheet": "mock_board", "id_card": "mock_govt"}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--type", required=True, choices=sorted(CREDENTIAL_TYPES))
    parser.add_argument("--holder-keys", required=True, help="pubkeys.json from wallet.export_holder_pubkeys")
    parser.add_argument("--out", help="batch output path")
    parser.parse_args(argv)
    raise SystemExit("issue: not implemented yet (TRUST step 1)")


if __name__ == "__main__":
    main()
