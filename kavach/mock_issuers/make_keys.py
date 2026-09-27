"""Create Ed25519 keypairs for the mock issuers (mock_bank, mock_board, mock_govt) under keys/issuers/.

Existing keys are never replaced. Also writes keys/issuers/trusted_issuers.json (iss -> public key), the file a
verifier copies to trust these issuers; `--export PATH` writes it somewhere else too (e.g. the requester's data dir).

Usage: python -m kavach.mock_issuers.make_keys [--export requester/data/trusted_issuers.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from kavach import config
from kavach.trust import crypto

ISSUERS = ("mock_bank", "mock_board", "mock_govt")


def issuers_dir() -> Path:
    return config.KEYS_DIR / "issuers"


def ensure_keys() -> dict[str, str]:
    """Create any missing issuer key; return the trust list iss -> public key."""
    d = issuers_dir()
    d.mkdir(parents=True, exist_ok=True)
    for iss in ISSUERS:
        path = d / f"{iss}.key"
        try:
            with open(path, "xb") as f:
                f.write(crypto.new_private_key())
        except FileExistsError:
            pass
    trust = {iss: crypto.public_key(issuer_private_key(iss)) for iss in ISSUERS}
    (d / "trusted_issuers.json").write_text(json.dumps(trust, indent=2), encoding="utf-8")
    return trust


def issuer_private_key(iss: str) -> bytes:
    if iss not in ISSUERS:
        raise ValueError(f"unknown issuer {iss!r}")
    key = (issuers_dir() / f"{iss}.key").read_bytes()
    if len(key) != 32:
        raise ValueError(f"{iss}.key is not a 32-byte Ed25519 key")
    return key


def trust_list() -> dict[str, str]:
    """iss -> public key of the mock issuers on this machine ({} if keys were never made)."""
    path = issuers_dir() / "trusted_issuers.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def export_trust_list(dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(ensure_keys(), indent=2), encoding="utf-8")
    return dest


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--export", type=Path, help="also write the trust list here")
    args = parser.parse_args(argv)
    trust = ensure_keys()
    print(f"Issuer keys in {issuers_dir()}")
    print(json.dumps(trust, indent=2))
    if args.export:
        print(f"Trust list written to {export_trust_list(args.export)}")


if __name__ == "__main__":
    main()
