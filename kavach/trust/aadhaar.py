"""Aadhaar Paperless Offline e-KYC verification (CONTRACT §6.6). Fully offline; nothing is written to disk or logged.

The owner downloads the ZIP from UIDAI (myAadhaar -> Offline eKYC) and chooses a share code, which is the ZIP's
password (AES or ZipCrypto). It holds one XML:

    <OfflinePaperlessKyc referenceId="<last 4 Aadhaar digits><YYYYMMDDHHMMSSmmm, IST>">
      <UidData><Poi name dob gender e m/><Poa .../><Pht>photo</Pht></UidData>
      <Signature xmlns="http://www.w3.org/2000/09/xmldsig#"> enveloped; rsa-sha1 over sha256 digests </Signature>
    </OfflinePaperlessKyc>

UIDAI's offline signing certificates (uidai.gov.in, Data and Downloads) are bundled in `certs/`; a file is checked
only against a certificate whose validity covers its referenceId time. RSA-SHA1 is allowed in `_UIDAI_SIGNATURE`
and nowhere else. Attributes are read from the element signxml returns as signed, never from the raw document, so a
second, unsigned copy of the data cannot be slipped in. Only name, date of birth, the last 4 digits and the
generation time leave this module; the photo, address, contact hashes and the XML itself are dropped here.
"""

from __future__ import annotations

import io
import re
import zipfile
import zlib
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import get_args

import pyzipper
from cryptography import x509
from cryptography.hazmat.primitives.serialization import Encoding
from lxml import etree
from signxml import DigestAlgorithm, SignatureConfiguration, SignatureMethod, XMLVerifier
from signxml.exceptions import SignXMLException

from kavach.models import AadhaarFailReason, AadhaarIdentity

CERT_DIR = Path(__file__).resolve().parent / "certs"
# The server's multipart parser keeps uploads under 1 MB in memory; real files are ~5-15 kB.
MAX_ZIP_BYTES = 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
ISSUER = "uidai"
IST = timezone(timedelta(hours=5, minutes=30))

# UIDAI signs with rsa-sha1 (older files: sha1 digests too). Allowed for this one verification, never globally.
_UIDAI_SIGNATURE = SignatureConfiguration(
    signature_methods=frozenset({SignatureMethod.RSA_SHA1, SignatureMethod.RSA_SHA256}),
    digest_algorithms=frozenset({DigestAlgorithm.SHA1, DigestAlgorithm.SHA256}),
)
_BAD_ZIP = (zipfile.BadZipFile, pyzipper.BadZipFile)  # pyzipper has its own class
_REFERENCE = re.compile(r"^(\d{4})(\d{17})$")
_SAFE_PARSER = etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False, load_dtd=False)


class AadhaarError(ValueError):
    """Why an offline e-KYC file was not accepted. `reason` is safe to show and to audit; it never holds content."""

    def __init__(self, reason: AadhaarFailReason) -> None:
        assert reason in get_args(AadhaarFailReason)
        super().__init__(reason)
        self.reason: AadhaarFailReason = reason


def _certificates() -> list[x509.Certificate]:
    out = []
    for path in sorted(CERT_DIR.glob("*.cer")):
        data = path.read_bytes()
        out.append(x509.load_pem_x509_certificate(data) if data.lstrip().startswith(b"-----")
                   else x509.load_der_x509_certificate(data))
    return out


def _xml_from_zip(zip_bytes: bytes, share_code: str) -> bytes:
    if len(zip_bytes) > MAX_ZIP_BYTES:
        raise AadhaarError("too_large")
    try:
        archive = pyzipper.AESZipFile(io.BytesIO(zip_bytes))
    except (*_BAD_ZIP, ValueError, OSError):
        raise AadhaarError("not_a_zip") from None
    with archive:
        members = [i for i in archive.infolist() if not i.is_dir() and i.filename.lower().endswith(".xml")]
        if not members:
            raise AadhaarError("no_xml")
        if len(members) > 1:
            raise AadhaarError("malformed")
        if members[0].file_size > MAX_XML_BYTES:
            raise AadhaarError("too_large")
        archive.setpassword(share_code.encode("utf-8"))
        try:
            with archive.open(members[0]) as f:
                data = f.read(MAX_XML_BYTES + 1)  # the header's size can lie
        except RuntimeError:  # "Bad password", or an encrypted member opened without one
            raise AadhaarError("wrong_share_code") from None
        except (*_BAD_ZIP, zlib.error, ValueError):  # ZipCrypto: a wrong code can pass the check byte
            raise AadhaarError("wrong_share_code") from None
    if len(data) > MAX_XML_BYTES:
        raise AadhaarError("too_large")
    return data


def _generated_at(reference_id: str) -> tuple[str, datetime]:
    """(last 4 digits, generation time in UTC) from referenceId."""
    m = _REFERENCE.match(reference_id or "")
    if not m:
        raise AadhaarError("malformed")
    try:
        local = datetime.strptime(m.group(2)[:14], "%Y%m%d%H%M%S").replace(tzinfo=IST)
    except ValueError:
        raise AadhaarError("malformed") from None
    return m.group(1), local.astimezone(timezone.utc)


def _dob(value: str) -> str | None:
    """"14-05-2003" / "14/05/2003" -> 2003-05-14; a year alone stays "2003"; anything else None."""
    value = (value or "").strip()
    if re.fullmatch(r"\d{4}", value):
        return value
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", value)
    if not m:
        return None
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).date().isoformat()
    except ValueError:
        return None


def verify_okyc(zip_bytes: bytes, share_code: str) -> AadhaarIdentity:
    """The owner's name, date of birth, last 4 digits and generation time from a verified offline e-KYC ZIP."""
    data = _xml_from_zip(zip_bytes, share_code)
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:  # no DTDs: entity expansion or external references
        raise AadhaarError("malformed")
    try:
        root = etree.fromstring(data, parser=_SAFE_PARSER)
    except etree.XMLSyntaxError:
        raise AadhaarError("malformed") from None
    last4, generated = _generated_at(root.get("referenceId", ""))

    signed = None
    for cert in _certificates():
        if not cert.not_valid_before_utc <= generated <= cert.not_valid_after_utc:
            continue
        try:
            result = XMLVerifier().verify(data, x509_cert=cert.public_bytes(Encoding.PEM).decode(),
                                          expect_config=_UIDAI_SIGNATURE)
        except (SignXMLException, etree.LxmlError, ValueError):
            continue
        signed = result.signed_xml
        break
    if signed is None or etree.QName(signed).localname != "OfflinePaperlessKyc"             or signed.get("referenceId") != root.get("referenceId"):
        raise AadhaarError("bad_signature")

    poi = signed.find("./UidData/Poi")
    if poi is None or not (poi.get("name") or "").strip():
        raise AadhaarError("malformed")
    return AadhaarIdentity(name=" ".join(poi.get("name").split()), dob=_dob(poi.get("dob", "")), last4=last4,
                           generated_at=generated.strftime("%Y-%m-%dT%H:%M:%SZ"))
