"""TRUST steps 1-2: crypto, mock issuers, signed PDFs, issuer_check, wallet, presentations and attestations."""

import json

import pymupdf
import pytest

from kavach import config
from kavach.mock_issuers import issue, make_keys
from kavach.models import Attestation, Claim, CredentialRef, Presentation
from kavach.textnorm import pdf_text_hash
from kavach.trust import crypto, issuer_check, present, wallet


@pytest.fixture
def trust_env(fresh_db, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "KEYS_DIR", tmp_path / "keys")
    monkeypatch.setattr(config, "DEMO_DATA_DIR", tmp_path / "demo_data")
    make_keys.ensure_keys()
    return tmp_path


def _stock(credential_type="income_proof", n=3):
    pubkeys = json.loads(wallet.export_holder_pubkeys(n).read_text())
    batch = issue.issue_batch(credential_type, pubkeys)
    path = config.KEYS_DIR / f"batch_{credential_type}.json"
    path.write_text(json.dumps(batch))
    assert wallet.import_batch(path) == n
    return batch


# --- crypto ------------------------------------------------------------------------------------------------


def test_sign_verify_roundtrip_and_tamper():
    priv = crypto.new_private_key()
    pub = crypto.public_key(priv)
    obj = {"b": 1, "a": "₹"}
    sig = crypto.sign(priv, obj)
    assert crypto.verify(pub, {"a": "₹", "b": 1}, sig)  # key order is irrelevant (canonical)
    assert not crypto.verify(pub, {"a": "₹", "b": 2}, sig)
    assert not crypto.verify(crypto.public_key(crypto.new_private_key()), obj, sig)
    assert crypto.verify(pub, b"rq_1|123", crypto.sign(priv, b"rq_1|123"))


@pytest.mark.parametrize("pub,sig", [("not-b64!", "x"), ("", ""), ("AAAA", "AAAA"), (None, None)])
def test_verify_never_raises(pub, sig):
    assert crypto.verify(pub, {}, sig) is False


def test_fingerprint_is_16_hex_of_raw_key():
    pub = crypto.public_key(crypto.new_private_key())
    fp = crypto.fingerprint(pub)
    assert len(fp) == 16 and int(fp, 16) >= 0
    with pytest.raises(ValueError):
        crypto.fingerprint(crypto.b64e(b"short"))


# --- issuers + wallet --------------------------------------------------------------------------------------


def test_make_keys_is_idempotent(trust_env):
    first = make_keys.ensure_keys()
    assert set(first) == set(make_keys.ISSUERS)
    assert make_keys.ensure_keys() == first == make_keys.trust_list()


def test_claims_follow_profile():
    p = dict(issue.PROFILE, monthly_income=62000)
    c = issue.claims_for("income_proof", p)
    assert c["income_ge_50000"] and not c["income_ge_75000"] and c["loan_default_12m"] is False
    from datetime import date
    assert issue.claims_for("id_card", p, today=date(2026, 9, 27)) == {"age_over_18": True, "age_over_21": True}
    assert issue.claims_for("marksheet", p)["board"] == p["board"]


def test_batch_copies_are_unlinkable(trust_env):
    batch = _stock(n=3)
    creds = [c["credential"] for c in batch["copies"]]
    assert len({c["holder_pubkey"] for c in creds}) == 3
    assert not set(creds[0]["digests"]) & set(creds[1]["digests"])
    assert len({c["issuer_sig"] for c in creds}) == 3
    body = {k: v for k, v in creds[0].items() if k != "issuer_sig"}
    assert crypto.verify(make_keys.trust_list()["mock_bank"], body, creds[0]["issuer_sig"])


def test_wallet_keeps_private_keys_and_counts(trust_env, monkeypatch):
    pubkeys_file = wallet.export_holder_pubkeys(2)
    assert all(len(crypto.b64d(p)) == 32 for p in json.loads(pubkeys_file.read_text()))
    assert "holder_privkey" not in pubkeys_file.read_text()
    _stock(n=2)
    monkeypatch.setattr(config, "WALLET_LOW_COPIES", 3)
    st = wallet.status()
    assert [(t.credential_type, t.unused, t.total) for t in st.by_type] == [("income_proof", 2, 2)]
    assert st.low == ["income_proof"]
    ref = wallet.find_copy("income_ge_50000")
    assert ref is not None and wallet.disclosed_value(ref, "income_ge_50000") is True
    assert wallet.find_copy("age_over_18") is None


def test_import_rejects_foreign_or_tampered_batch(trust_env):
    pubkeys = json.loads(wallet.export_holder_pubkeys(1).read_text())
    batch = issue.issue_batch("income_proof", pubkeys)
    batch["copies"][0]["disclosures"][0]["value"] = not batch["copies"][0]["disclosures"][0]["value"]
    path = trust_env / "bad.json"
    path.write_text(json.dumps(batch))
    with pytest.raises(wallet.WalletError):
        wallet.import_batch(path)
    foreign = issue.issue_batch("income_proof", [crypto.public_key(crypto.new_private_key())])
    path.write_text(json.dumps(foreign))
    assert wallet.import_batch(path) == 0  # not our holder key


# --- PDFs --------------------------------------------------------------------------------------------------


def test_signed_tampered_and_plain_pdfs(trust_env):
    out = trust_env / "generated"
    paths = {p.name: p for p in issue.make_pdfs(out)}
    for name in ("bank_statement_signed.pdf", "marksheet_signed.pdf", "id_card_signed.pdf"):
        res = issuer_check.verify_pdf(paths[name])
        assert res.status == "issuer_signed" and res.iss in make_keys.ISSUERS, name
    tampered = issuer_check.verify_pdf(paths["bank_statement_TAMPERED.pdf"])
    assert tampered.status == "invalid" and tampered.iss == "mock_bank"
    plain = trust_env / "plain.pdf"
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), "hello")
    doc.save(plain)
    doc.close()
    assert issuer_check.verify_pdf(plain).status == "unsigned"
    assert issuer_check.verify_pdf(trust_env / "missing.pdf").status == "unsigned"


def test_untrusted_issuer_is_invalid(trust_env):
    pdf = next(p for p in issue.make_pdfs(trust_env / "g") if p.name == "id_card_signed.pdf")
    with pymupdf.open(pdf) as doc:
        doc.set_metadata({**doc.metadata, "keywords": json.dumps({"iss": "evil_bank", "sig": "AAAA"})})
        doc.saveIncr()
    assert issuer_check.verify_pdf(pdf).status == "invalid"
    assert pdf_text_hash(pdf)


def test_rent_agreement_rendered_from_template(trust_env):
    tpl = config.DEMO_DATA_DIR / "templates"
    tpl.mkdir(parents=True)
    (tpl / "rent_agreement.md").write_text("Landlord: Ramesh Kumar\nRent: INR 14,500", encoding="utf-8")
    names = [p.name for p in issue.make_pdfs(trust_env / "g")]
    assert "rent_agreement.pdf" in names


# --- presentations + attestations --------------------------------------------------------------------------


def test_presentation_binds_nonce_and_uses_copy(trust_env):
    _stock(n=2)
    ref = wallet.find_copy("income_ge_50000")
    pres = present.build_presentation(ref, "income_ge_50000", "nonce-1", "fp-landlord")
    p = Presentation.model_validate(pres)
    assert [d.claim for d in p.disclosures] == ["income_ge_50000"]
    payload = present.binding_payload(pres["credential"], pres["disclosures"], "nonce-1", "fp-landlord",
                                      pres["binding"]["iat"])
    assert crypto.verify(pres["credential"]["holder_pubkey"], payload, pres["binding"]["sig"])
    assert "salt" in pres["disclosures"][0] and len(pres["disclosures"]) == 1  # nothing else disclosed
    with pytest.raises(present.PresentError):
        present.build_presentation(ref, "income_ge_50000", "n2", "fp")  # copy already used
    assert wallet.find_copy("income_ge_50000").cred_id != ref.cred_id
    assert wallet.status().by_type[0].unused == 1


def test_attestation_needs_paired_requester(trust_env, fresh_db):
    claim = Claim(claim="income", op="ge", value=60000)
    with pytest.raises(present.PresentError):
        present.build_attestation(claim, True, "fp1", "n")
    priv = crypto.new_private_key()
    fresh_db.insert("requesters", {"fingerprint": "fp1", "pubkey": "x", "name": "R", "type": "landlord",
                                   "status": "paired", "owner_pairwise_privkey": priv, "paired_at": "2026-09-27T00:00:00Z"})
    att = present.build_attestation(Claim(claim="income", op="ge", value=60000, issuer_claim=None), True, "fp1", "n")
    a = Attestation.model_validate(att)
    assert a.owner_pairwise_pubkey == crypto.public_key(priv) and a.aud == "fp1"
    assert crypto.verify(a.owner_pairwise_pubkey, {k: v for k, v in att.items() if k != "sig"}, att["sig"])
    assert att["claim"] == {"claim": "income", "op": "ge", "value": 60000}


def test_find_copy_ref_shape(trust_env):
    _stock("id_card", n=1)
    ref = wallet.find_copy("age_over_18")
    assert isinstance(ref, CredentialRef) and ref.iss == "mock_govt"
