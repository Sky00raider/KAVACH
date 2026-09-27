"""Credential copies and their one-time holder keys (CONTRACT §6.1). Private keys never leave the wallet.

Issuance: `export_holder_pubkeys(n)` makes n one-time keypairs, keeps the private halves under keys/wallet/pending/
and writes only the public keys to a pubkeys.json for the issuer. `import_batch(path)` checks each issued copy
(issuer signature, digests match the disclosures, holder key is one we generated) and stores it with its key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from kavach import config, db
from kavach.db import new_id
from kavach.models import CredentialRef, WalletStatus, WalletTypeStatus
from kavach.trust import crypto


class WalletError(Exception):
    pass


def _wallet_dir() -> Path:
    return config.KEYS_DIR / "wallet"


def _pending_dir() -> Path:
    return _wallet_dir() / "pending"


def export_holder_pubkeys(n: int = 20) -> Path:
    """Write n fresh one-time holder public keys to a pubkeys.json for an issuer; returns its path."""
    if not 1 <= n <= 1000:
        raise ValueError("n must be between 1 and 1000")
    pending = _pending_dir()
    pending.mkdir(parents=True, exist_ok=True)
    pubkeys = []
    for _ in range(n):
        priv = crypto.new_private_key()
        pub = crypto.public_key(priv)
        (pending / f"{crypto.fingerprint(pub)}.key").write_bytes(priv)
        pubkeys.append(pub)
    out = _wallet_dir() / f"pubkeys_{new_id('pk')}.json"
    out.write_text(json.dumps(pubkeys, indent=1), encoding="utf-8")
    return out


def _issuer_trust() -> dict[str, str]:
    from kavach.mock_issuers.make_keys import trust_list

    return trust_list()


def _check_copy(copy: dict[str, Any], trust: dict[str, str]) -> None:
    from kavach.mock_issuers.issue import digest

    cred = copy["credential"]
    body = {k: v for k, v in cred.items() if k != "issuer_sig"}
    pub = trust.get(cred["iss"])
    if pub is not None and not crypto.verify(pub, body, cred["issuer_sig"]):
        raise WalletError(f"copy {cred['copy']}: issuer signature does not verify")
    digests = set(cred["digests"])
    for d in copy["disclosures"]:
        if digest(d["salt"], d["claim"], d["value"]) not in digests:
            raise WalletError(f"copy {cred['copy']}: disclosure {d['claim']} does not match a digest")


def import_batch(path: Path) -> int:
    """Store every copy whose holder key this wallet generated; returns the number of copies stored."""
    batch = json.loads(Path(path).read_text(encoding="utf-8"))
    trust = _issuer_trust()
    stored = 0
    for copy in batch["copies"]:
        _check_copy(copy, trust)
        cred = copy["credential"]
        key_file = _pending_dir() / f"{crypto.fingerprint(cred['holder_pubkey'])}.key"
        if not key_file.is_file():
            continue  # not ours, or already imported
        priv = key_file.read_bytes()
        if crypto.public_key(priv) != cred["holder_pubkey"]:
            raise WalletError(f"copy {cred['copy']}: holder key mismatch")
        db.insert("credentials", {
            "cred_id": new_id("cr"), "iss": cred["iss"], "credential_type": cred["credential_type"],
            "copy": cred["copy"], "credential_json": json.dumps(cred), "disclosures_json": json.dumps(copy["disclosures"]),
            "holder_privkey": priv, "used": 0,
        })
        key_file.unlink()
        stored += 1
    return stored


def find_copy(issuer_claim: str) -> CredentialRef | None:
    """An unused copy whose disclosures include `issuer_claim`, lowest copy number first."""
    rows = db.fetch_all("SELECT cred_id, iss, credential_type, copy, disclosures_json FROM credentials "
                        "WHERE used = 0 ORDER BY credential_type, copy")
    for r in rows:
        if any(d["claim"] == issuer_claim for d in json.loads(r["disclosures_json"])):
            return CredentialRef(cred_id=r["cred_id"], iss=r["iss"], credential_type=r["credential_type"],
                                 copy=r["copy"])
    return None


def disclosed_value(ref: CredentialRef, issuer_claim: str) -> Any:
    """The value the issuer signed for `issuer_claim` in this copy (raises KeyError if absent)."""
    for d in load(ref.cred_id)["disclosures"]:
        if d["claim"] == issuer_claim:
            return d["value"]
    raise KeyError(issuer_claim)


def load(cred_id: str) -> dict[str, Any]:
    """{credential, disclosures, holder_privkey, used} for one copy. Private: only present.py uses the key."""
    row = db.fetch_one("SELECT * FROM credentials WHERE cred_id = ?", (cred_id,))
    if row is None:
        raise KeyError(cred_id)
    return {"credential": json.loads(row["credential_json"]), "disclosures": json.loads(row["disclosures_json"]),
            "holder_privkey": row["holder_privkey"], "used": bool(row["used"]),
            "credential_type": row["credential_type"]}


def claim_copy(cred_id: str) -> bool:
    """used 0 -> 1 in one statement; False when someone else already used it."""
    with db.connect() as conn:
        return conn.execute("UPDATE credentials SET used = 1 WHERE cred_id = ? AND used = 0", (cred_id,)).rowcount == 1


def status() -> WalletStatus:
    rows = db.fetch_all("SELECT credential_type, iss, SUM(used = 0) AS unused, COUNT(*) AS total FROM credentials "
                        "GROUP BY credential_type, iss ORDER BY credential_type")
    by_type = [WalletTypeStatus(credential_type=r["credential_type"], iss=r["iss"], unused=int(r["unused"] or 0),
                                total=int(r["total"])) for r in rows]
    return WalletStatus(by_type=by_type, low=[t.credential_type for t in by_type if t.unused < config.WALLET_LOW_COPIES])
