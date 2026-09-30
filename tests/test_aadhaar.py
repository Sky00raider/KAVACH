"""Aadhaar offline e-KYC (CONTRACT §6.6): trust.aadhaar verifies, brain.identity makes it the anchor.

Files here are synthetic and signed with a self-signed test certificate the way UIDAI signs (enveloped, rsa-sha1 over
sha256 digests). A real file is only ever checked by hand with scripts/check_aadhaar.py, from private/.
"""

import base64
import io
import json
import shutil
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import numpy as np
import pytest
import pyzipper
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.x509.oid import NameOID
from fastapi.testclient import TestClient
from lxml import etree
from signxml import DigestAlgorithm, SignatureMethod, XMLSigner, XMLVerifier, methods
from signxml.exceptions import InvalidInput

from kavach import api, config
from kavach.brain import decide, identity, ingest, llm
from kavach.mock_issuers import issue
from kavach.models import Claim
from kavach.trust import aadhaar, audit

DS = "http://www.w3.org/2000/09/xmldsig#"
C14N = "http://www.w3.org/TR/2001/REC-xml-c14n-20010315"
SHARE = "4821"
REAL_AUDIT_LOG = audit.log
NOW_IST = datetime.now(timezone(timedelta(hours=5, minutes=30)))
REFERENCE = "7310" + NOW_IST.strftime("%Y%m%d%H%M%S") + "123"


def _cert(key, days_before=1, days_after=365):
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "KAVACH test UIDAI")])
    now = datetime.now(timezone.utc)
    return (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=days_before))
            .not_valid_after(now + timedelta(days=days_after)).sign(key, hashes.SHA256()))


@pytest.fixture(scope="module")
def uidai_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def certs(tmp_path, monkeypatch, uidai_key):
    """The test certificate is the only trusted UIDAI certificate."""
    d = tmp_path / "certs"
    d.mkdir()
    (d / "test_uidai.cer").write_bytes(_cert(uidai_key).public_bytes(serialization.Encoding.PEM))
    monkeypatch.setattr(aadhaar, "CERT_DIR", d)
    return d


def kyc_xml(key, name="Ananya Iyer", dob="14-05-2003", reference=REFERENCE) -> bytes:
    """An offline e-KYC XML signed like UIDAI's: enveloped, c14n, sha256 digest, rsa-sha1 SignatureValue."""
    root = etree.fromstring(
        f'<OfflinePaperlessKyc referenceId="{reference}"><UidData>'
        f'<Poi dob="{dob}" e="aa11" gender="F" m="bb22" name="{name}"/>'
        f'<Poa careof="" country="India" dist="Bengaluru" house="402" pc="560001" state="Karnataka" '
        f'street="MG Road" vtc="Bengaluru"/><Pht>UEhPVE9CWVRFUw==</Pht></UidData></OfflinePaperlessKyc>'.encode())
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    signed = XMLSigner(method=methods.enveloped, signature_algorithm=SignatureMethod.RSA_SHA256,
                       digest_algorithm=DigestAlgorithm.SHA256, c14n_algorithm=C14N).sign(root, key=key_pem)
    # signxml will not sign with SHA-1; UIDAI does, so re-sign SignedInfo with rsa-sha1 as UIDAI's files are
    info = signed.find(f".//{{{DS}}}SignedInfo")
    info.find(f"{{{DS}}}SignatureMethod").set("Algorithm", f"{DS}rsa-sha1")
    sig = key.sign(etree.tostring(info, method="c14n"), padding.PKCS1v15(), hashes.SHA1())
    signed.find(f".//{{{DS}}}SignatureValue").text = base64.b64encode(sig).decode()
    return etree.tostring(signed, xml_declaration=True, encoding="UTF-8", standalone=True)


def kyc_zip(xml: bytes | None, share=SHARE, extra: dict | None = None) -> bytes:
    buf = io.BytesIO()
    with pyzipper.AESZipFile(buf, "w", compression=pyzipper.ZIP_DEFLATED, encryption=pyzipper.WZ_AES) as z:
        z.setpassword(share.encode())
        if xml is not None:
            z.writestr(f"offlineaadhaar{REFERENCE[4:]}.xml", xml)
        for n, content in (extra or {}).items():
            z.writestr(n, content)
    return buf.getvalue()


def _reason(zip_bytes, share=SHARE):
    with pytest.raises(aadhaar.AadhaarError) as exc:
        aadhaar.verify_okyc(zip_bytes, share)
    return exc.value.reason


# --- trust.aadhaar --------------------------------------------------------------------------------------------


def test_verifies_and_keeps_only_name_dob_last4_time(certs, uidai_key):
    out = aadhaar.verify_okyc(kyc_zip(kyc_xml(uidai_key)), SHARE)
    assert out.model_dump() == {"name": "Ananya Iyer", "dob": "2003-05-14", "last4": "7310",
                                "generated_at": NOW_IST.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}


def test_year_only_date_of_birth(certs, uidai_key):
    assert aadhaar.verify_okyc(kyc_zip(kyc_xml(uidai_key, dob="2003")), SHARE).dob == "2003"


def test_rsa_sha1_is_accepted_only_on_the_uidai_path(certs, uidai_key):
    xml = kyc_xml(uidai_key)
    pem = (certs / "test_uidai.cer").read_text()
    with pytest.raises(InvalidInput, match="SHA1"):
        XMLVerifier().verify(xml, x509_cert=pem)  # signxml's global default still refuses SHA-1
    assert aadhaar.verify_okyc(kyc_zip(xml), SHARE).name == "Ananya Iyer"


@pytest.mark.parametrize("make, reason", [
    (lambda key: b"not a zip at all", "not_a_zip"),
    (lambda key: kyc_zip(None, extra={"readme.txt": b"hi"}), "no_xml"),
    (lambda key: kyc_zip(kyc_xml(key), extra={"second.xml": b"<a/>"}), "malformed"),
    (lambda key: b"PK" + b"\0" * (aadhaar.MAX_ZIP_BYTES + 10), "too_large"),
    (lambda key: kyc_zip(b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><x>&a;</x>'), "malformed"),
    (lambda key: kyc_zip(kyc_xml(key, reference="12345")), "malformed"),
])
def test_rejects_bad_files(certs, uidai_key, make, reason):
    assert _reason(make(uidai_key)) == reason


def test_wrong_share_code(certs, uidai_key):
    assert _reason(kyc_zip(kyc_xml(uidai_key)), share="0000") == "wrong_share_code"


def test_tampered_or_foreign_signature_is_rejected(certs, uidai_key):
    tampered = kyc_xml(uidai_key).replace(b'name="Ananya Iyer"', b'name="Rohan Mehta"')
    assert _reason(kyc_zip(tampered)) == "bad_signature"
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    assert _reason(kyc_zip(kyc_xml(other))) == "bad_signature"


def test_certificate_must_cover_the_generation_time(certs, uidai_key):
    old = "7310" + "20190101120000000"  # before the test certificate's validity
    assert _reason(kyc_zip(kyc_xml(uidai_key, reference=old))) == "bad_signature"


def test_bundled_uidai_certificates():
    certs = aadhaar._certificates()
    assert len(certs) == 5
    assert all("UNIQUE IDENTIFICATION AUTHORITY OF INDIA" in c.subject.rfc4514_string().upper() for c in certs)
    assert max(c.not_valid_after_utc for c in certs).year == 2029  # uidai_offline_publickey_2026.cer


# --- brain.identity: the Aadhaar anchor ------------------------------------------------------------------------


@pytest.fixture
def vault(fresh_db, tmp_path, monkeypatch, certs):
    root = tmp_path / "vault"
    for sub in ("pdfs", "notes", "chats"):
        (root / sub).mkdir(parents=True)
    for attr, sub in {"VAULT_DIR": "vault", "KEYS_DIR": "keys", "DEMO_DATA_DIR": "demo_data"}.items():
        monkeypatch.setattr(config, attr, tmp_path / sub)
    monkeypatch.setattr(llm, "embed", lambda texts: np.ones((len(texts), config.EMBED_DIM), dtype=np.float32))
    monkeypatch.setattr(audit, "log", REAL_AUDIT_LOG)

    def signed(kind: str, file: str, **profile):
        out = tmp_path / "issued" / file
        issue.make_pdfs(out, {**issue.PROFILE, **profile})
        dest = root / "pdfs" / f"{file}.pdf"
        shutil.copy(out / f"{kind}.pdf", dest)
        return dest

    return SimpleNamespace(root=root, signed=signed, db=fresh_db)


def test_aadhaar_becomes_the_anchor_masked(vault, uidai_key):
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    ingest.ingest_file(vault.signed("bank_statement_signed", "bank_statement_signed"))
    out = identity.import_aadhaar(kyc_zip(kyc_xml(uidai_key)), SHARE)
    assert (out.status, out.source, out.issuer, out.name_initials, out.birth_year, out.doc_id) == \
        ("verified", "aadhaar_okyc", "uidai", "A. I.", 2003, None)
    assert "7310" not in out.model_dump_json()
    row = vault.db.live_identity()
    assert (row["name"], row["dob"], row["last4"], row["source"]) == ("Ananya Iyer", "2003-05-14", "7310", "aadhaar_okyc")
    # same person: the signed documents stay the owner's
    assert {d.holder_status for d in vault.db.list_documents()} == {"verified"}

    rows = vault.db.fetch_all("SELECT ref_id, detail_json FROM audit_log WHERE event = 'identity_verified'")
    assert len(rows) == 1 and rows[0]["ref_id"] == row["identity_id"]
    assert set(json.loads(rows[0]["detail_json"])) == {"source", "issuer", "verified_at"}


def test_aadhaar_in_another_name_rechecks_every_signed_document(vault, uidai_key):
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    ingest.ingest_file(vault.signed("bank_statement_signed", "bank_statement_signed"))
    assert decide.decide(Claim(claim="income", op="ge", value=45000), "fp").answer_type == "OWNER_ATTESTED"
    identity.import_aadhaar(kyc_zip(kyc_xml(uidai_key, name="Rohan Mehta", dob="02-11-2001")), SHARE)
    assert {d.holder_status for d in vault.db.list_documents()} == {"mismatch"}
    assert decide.decide(Claim(claim="income", op="ge", value=45000), "fp").answer_type == "CANNOT_CONFIRM"


def test_year_only_aadhaar_dob_checks_the_year(vault, uidai_key):
    identity.import_aadhaar(kyc_zip(kyc_xml(uidai_key, dob="2003")), SHARE)
    assert ingest.ingest_file(vault.signed("marksheet_signed", "marks")).holder_status == "verified"
    other = ingest.ingest_file(vault.signed("marksheet_signed", "marks_other", date_of_birth="2001-05-14"))
    assert other.holder_status == "mismatch"


def test_an_id_card_never_replaces_the_aadhaar_anchor(vault, uidai_key):
    identity.import_aadhaar(kyc_zip(kyc_xml(uidai_key)), SHARE)
    ingest.ingest_file(vault.signed("id_card_signed", "id_card"))
    assert identity.current().source == "aadhaar_okyc"


def test_failed_import_is_audited_without_content(vault, uidai_key):
    with pytest.raises(aadhaar.AadhaarError):
        identity.import_aadhaar(kyc_zip(kyc_xml(uidai_key)), "9999")
    rows = vault.db.fetch_all("SELECT ref_id, detail_json FROM audit_log WHERE event = 'identity_verify_failed'")
    assert [(r["ref_id"], r["detail_json"]) for r in rows] == \
        [(None, '{"reason":"wrong_share_code","source":"aadhaar_okyc"}')]
    assert identity.current().source == "config"


def test_nothing_but_the_minimum_reaches_the_database(vault, uidai_key):
    identity.import_aadhaar(kyc_zip(kyc_xml(uidai_key)), SHARE)
    with vault.db.connect() as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    raw = config.DB_PATH.read_bytes()
    for leaked in (b"560001", b"MG Road", b"UEhPVE9CWVRFUw==", b"bb22", b"aa11", SHARE.encode() + b'"'):
        assert leaked not in raw, leaked


def test_aadhaar_route(vault, uidai_key, monkeypatch):
    monkeypatch.setattr(api, "_ollama_status", lambda: (False, set()))
    client = TestClient(api.app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8000")
    token = {"X-Owner-Token": "test-owner-token"}

    def post(zip_bytes, share=SHARE):
        return client.post("/api/identity/aadhaar", headers=token, data={"share_code": share},
                           files={"file": ("offlineaadhaar.zip", zip_bytes, "application/zip")})

    ok = post(kyc_zip(kyc_xml(uidai_key)))
    assert ok.status_code == 200 and ok.json()["source"] == "aadhaar_okyc"
    assert "Ananya" not in ok.text and "7310" not in ok.text and "2003-05-14" not in ok.text
    assert (post(kyc_zip(kyc_xml(uidai_key)), "0000").status_code, post(b"nope").json()) == \
        (400, {"detail": "not_a_zip"})
    bad = post(kyc_zip(kyc_xml(uidai_key).replace(b"Ananya Iyer", b"Rohan Mehta")))
    assert (bad.status_code, bad.json()) == (422, {"detail": "bad_signature"})
    assert client.post("/api/identity/aadhaar", data={"share_code": SHARE},
                       files={"file": ("a.zip", b"x", "application/zip")}).status_code == 401


def test_check_script_output_is_masked(certs, uidai_key, tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_aadhaar", config.ROOT / "scripts" / "check_aadhaar.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    z = tmp_path / "offlineaadhaar.zip"
    z.write_bytes(kyc_zip(kyc_xml(uidai_key)))
    out = script.check(z, SHARE)
    assert out == "verified: yes | issuer: uidai | name: A. I. | born: 2003"
    assert script.check(z, "0000") == "verified: no (wrong_share_code)"
