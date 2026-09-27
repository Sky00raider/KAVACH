"""Mock issuers: signed PDFs (CONTRACT §6.5) and credential batches (§6.1).

Credential batch (issuance flow in §6.1):
    wallet.export_holder_pubkeys(n=20) -> pubkeys.json
    python -m kavach.mock_issuers.issue --type income_proof --holder-keys pubkeys.json [--out batch.json]
    wallet.import_batch(batch.json)

Signed PDFs (bank statement, marksheet, ID card, the tampered statement, an unsigned rent agreement):
    python -m kavach.mock_issuers.issue --pdfs demo_data/generated

The subject's details come from PROFILE, overridable by demo_data/issuer_profile.json (same keys).
Issuers are simulated; the mechanism (Ed25519 over salted digests / the PDF text hash) is real.
"""

from __future__ import annotations

import argparse
import json
import random
import secrets
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pymupdf

from kavach import config
from kavach.mock_issuers.make_keys import ensure_keys, issuer_private_key
from kavach.textnorm import pdf_text_hash
from kavach.trust import crypto

CREDENTIAL_TYPES = {"income_proof": "mock_bank", "marksheet": "mock_board", "id_card": "mock_govt"}
ISSUER_NAMES = {"mock_bank": "Mock Bank of India", "mock_board": "Mock Board of Pre-University Education",
                "mock_govt": "Mock Government ID Authority"}
VALIDITY_DAYS = 180

PROFILE: dict[str, Any] = {
    "name": "Ananya Iyer",
    "account": "XXXXXXXX4821",
    "employer": "Nimbus Analytics Pvt Ltd",
    "monthly_income": 62000,
    "loan_default_12m": False,
    "landlord": "Ravi Kumar",
    "rent_amount": 14500,
    "date_of_birth": "2003-05-14",
    "id_number": "XXXX-XXXX-7310",
    "id_expiry": "2033-05-13",
    "percentage": 82.4,
    "result": "PASS",
    "board": "Mock Board of Pre-University Education",
    "tampered_income": 92000,
}


def load_profile() -> dict[str, Any]:
    override = config.DEMO_DATA_DIR / "issuer_profile.json"
    profile = dict(PROFILE)
    if override.is_file():
        profile.update(json.loads(override.read_text(encoding="utf-8")))
    return profile


def _age(dob: str, today: date) -> int:
    born = date.fromisoformat(dob)
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


def claims_for(credential_type: str, profile: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """The issuer-provable claims (CONTRACT §5.1) this issuer signs, with their values for the subject."""
    today = today or datetime.now(timezone.utc).date()
    if credential_type == "income_proof":
        income = int(profile["monthly_income"])
        out: dict[str, Any] = {f"income_ge_{t}": income >= t for t in (25000, 50000, 75000, 100000)}
        out["loan_default_12m"] = bool(profile["loan_default_12m"])
        return out
    if credential_type == "marksheet":
        pct = float(profile["percentage"])
        out = {f"percentage_ge_{t}": pct >= t for t in (60, 75, 90)}
        out["result_pass"] = str(profile["result"]).upper() == "PASS"
        out["board"] = str(profile["board"])
        return out
    if credential_type == "id_card":
        age = _age(profile["date_of_birth"], today)
        return {"age_over_18": age >= 18, "age_over_21": age >= 21}
    raise ValueError(f"unknown credential type {credential_type!r}")


def digest(salt: str, claim: str, value: Any) -> str:
    """base64url sha256 of `f"{salt}|{claim}|{json.dumps(value)}"` (CONTRACT §6.1)."""
    return crypto.b64e(crypto.sha256(f"{salt}|{claim}|{json.dumps(value)}".encode()))


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def issue_copy(credential_type: str, copy_no: int, holder_pubkey: str, claims: dict[str, Any],
               now: datetime | None = None) -> dict[str, Any]:
    """One signed credential copy plus its disclosures (salt, claim, value) for the holder's wallet."""
    iss = CREDENTIAL_TYPES[credential_type]
    crypto.fingerprint(holder_pubkey)  # rejects anything that is not a 32-byte Ed25519 key
    now = now or datetime.now(timezone.utc)
    disclosures = [{"salt": crypto.b64e(secrets.token_bytes(16)), "claim": c, "value": v} for c, v in claims.items()]
    digests = [digest(d["salt"], d["claim"], d["value"]) for d in disclosures]
    random.SystemRandom().shuffle(digests)
    credential = {"iss": iss, "credential_type": credential_type, "copy": copy_no, "holder_pubkey": holder_pubkey,
                  "iat": _iso(now), "exp": _iso(now + timedelta(days=VALIDITY_DAYS)), "digests": digests}
    credential["issuer_sig"] = crypto.sign(issuer_private_key(iss), credential)
    return {"credential": credential, "disclosures": disclosures}


def issue_batch(credential_type: str, holder_pubkeys: list[str], profile: dict[str, Any] | None = None
                ) -> dict[str, Any]:
    """A batch: one copy per holder key, fresh salts per copy (unlinkable presentations)."""
    if credential_type not in CREDENTIAL_TYPES:
        raise ValueError(f"unknown credential type {credential_type!r}")
    if not holder_pubkeys:
        raise ValueError("no holder public keys")
    ensure_keys()
    claims = claims_for(credential_type, profile or load_profile())
    copies = [issue_copy(credential_type, i + 1, pk, claims) for i, pk in enumerate(holder_pubkeys)]
    return {"iss": CREDENTIAL_TYPES[credential_type], "credential_type": credential_type, "copies": copies}


# --- signed PDFs -------------------------------------------------------------------------------------------


def _inr(amount: float) -> str:
    """Indian digit grouping: 162000 -> 1,62,000.00"""
    whole, frac = f"{amount:.2f}".split(".")
    head, tail = whole[:-3], whole[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups + [tail]) + "." + frac if groups else f"{tail}.{frac}"


def _render(path: Path, title: str, lines: list[str]) -> None:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((56, 64), title, fontsize=16, fontname="helv")
    y = 96
    for line in lines:
        if y > 800:
            page = doc.new_page()
            y = 64
        page.insert_text((56, y), line, fontsize=10, fontname="cour")
        y += 15
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    doc.close()


def sign_pdf(path: Path, iss: str) -> None:
    """Sign the generated PDF's text hash (never the source text) and write it into metadata `keywords`."""
    sig = crypto.sign(issuer_private_key(iss), pdf_text_hash(path).encode())
    with pymupdf.open(path) as doc:
        meta = dict(doc.metadata or {})
        meta["keywords"] = json.dumps({"iss": iss, "sig": sig})
        meta["producer"] = ISSUER_NAMES[iss]
        doc.set_metadata(meta)
        doc.saveIncr()


def _statement_lines(p: dict[str, Any], income: int) -> list[str]:
    rows = []
    balance = 18250.00
    for month in ("2026-06", "2026-07", "2026-08"):
        balance += income
        rows.append(f"{month}-01  SALARY CREDIT {p['employer'][:22]:<22} {'':>12} {_inr(income):>12} {_inr(balance):>13}")
        balance -= p["rent_amount"]
        rows.append(f"{month}-05  UPI/RENT/{p['landlord'].upper()[:18]:<18}    {_inr(p['rent_amount']):>12} "
                    f"{'':>12} {_inr(balance):>13}")
        balance -= 9120.50
        rows.append(f"{month}-12  CARD/GROCERIES AND UTILITIES     {_inr(9120.50):>12} {'':>12} {_inr(balance):>13}")
    return [
        ISSUER_NAMES["mock_bank"] + "  -  Statement of Account",
        f"Account holder: {p['name']}",
        f"Account number: {p['account']}",
        "Period: 01 Jun 2026 to 31 Aug 2026",
        "",
        f"{'Date':<10}  {'Description':<32} {'Debit':>12} {'Credit':>12} {'Balance':>13}",
        *rows,
        "",
        f"Average monthly salary credit: INR {_inr(income)}",
        "Loan accounts: " + ("DEFAULT REPORTED in last 12 months" if p["loan_default_12m"]
                             else "no default in the last 12 months"),
        "This statement is digitally signed by the issuing bank.",
    ]


def make_pdfs(out_dir: Path, profile: dict[str, Any] | None = None) -> list[Path]:
    """bank_statement_signed, marksheet_signed, id_card_signed, bank_statement_TAMPERED (+ rent_agreement if the
    DATA template exists). Returns the paths written."""
    p = profile or load_profile()
    ensure_keys()
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []

    bank = out_dir / "bank_statement_signed.pdf"
    _render(bank, "Statement of Account", _statement_lines(p, int(p["monthly_income"])))
    sign_pdf(bank, "mock_bank")
    written.append(bank)

    marks = out_dir / "marksheet_signed.pdf"
    _render(marks, "Statement of Marks", [
        ISSUER_NAMES["mock_board"], f"Candidate: {p['name']}", f"Date of birth: {p['date_of_birth']}",
        "Examination: II PUC, March 2021", "",
        "Physics 84/100", "Chemistry 79/100", "Mathematics 88/100", "English 81/100", "Computer Science 80/100", "",
        f"Percentage: {p['percentage']}%", f"Result: {p['result']}", f"Board: {p['board']}",
    ])
    sign_pdf(marks, "mock_board")
    written.append(marks)

    idc = out_dir / "id_card_signed.pdf"
    _render(idc, "Identity Card", [
        ISSUER_NAMES["mock_govt"], f"Name: {p['name']}", f"Date of birth: {p['date_of_birth']}",
        f"ID number: {p['id_number']}", f"Valid until: {p['id_expiry']}",
    ])
    sign_pdf(idc, "mock_govt")
    written.append(idc)

    # Same statement with the salary edited, keeping the original signature: must verify as `invalid`.
    tampered = out_dir / "bank_statement_TAMPERED.pdf"
    _render(tampered, "Statement of Account", _statement_lines(p, int(p["tampered_income"])))
    with pymupdf.open(bank) as src:
        keywords = src.metadata["keywords"]
    with pymupdf.open(tampered) as doc:
        doc.set_metadata({**(doc.metadata or {}), "keywords": keywords})
        doc.saveIncr()
    written.append(tampered)

    template = next((t for t in (config.DEMO_DATA_DIR / "templates" / n for n in ("rent_agreement.md",
                                                                                     "rent_agreement.txt"))
                     if t.is_file()), None)
    if template is not None:
        rent = out_dir / "rent_agreement.pdf"
        _render(rent, "Rent Agreement", template.read_text(encoding="utf-8").splitlines())
        written.append(rent)
    return written


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--type", choices=sorted(CREDENTIAL_TYPES))
    parser.add_argument("--holder-keys", type=Path, help="pubkeys.json from wallet.export_holder_pubkeys")
    parser.add_argument("--out", type=Path, help="batch output path (default: next to --holder-keys)")
    parser.add_argument("--pdfs", type=Path, help="write the signed demo PDFs into this directory")
    args = parser.parse_args(argv)
    if not args.pdfs and not args.type:
        parser.error("give --type with --holder-keys, and/or --pdfs")
    if args.pdfs:
        for path in make_pdfs(args.pdfs):
            print(f"wrote {path}")
    if args.type:
        if not args.holder_keys:
            parser.error("--type needs --holder-keys")
        pubkeys = json.loads(args.holder_keys.read_text(encoding="utf-8"))
        batch = issue_batch(args.type, pubkeys)
        out = args.out or args.holder_keys.with_name(f"batch_{args.type}.json")
        out.write_text(json.dumps(batch, indent=1), encoding="utf-8")
        print(f"issued {len(batch['copies'])} {args.type} copies -> {out}")


if __name__ == "__main__":
    main()
