import hashlib
import json

import pymupdf

from kavach.textnorm import normalize_text, pdf_pages, pdf_text_hash, text_hash


def _make_pdf(path, pages):
    doc = pymupdf.open()
    for text in pages:
        doc.new_page().insert_text((72, 72), text)
    doc.save(path)
    doc.close()


def test_normalize_collapses_whitespace_and_strips():
    assert normalize_text("  Salary\tCredit \n\n 62,000.00  ") == "Salary Credit 62,000.00"


def test_normalize_applies_nfkc():
    # ligature, full-width digits, no-break space
    assert normalize_text("ﬁle １２ 000") == "file 12 000"


def test_normalize_empty():
    assert normalize_text(" \n\t ") == ""


def test_text_hash_is_sha256_of_normalised_utf8():
    assert text_hash("a  b\n") == hashlib.sha256("a b".encode("utf-8")).hexdigest()
    assert text_hash("₹ 15,000") == hashlib.sha256("₹ 15,000".encode("utf-8")).hexdigest()


def test_pdf_text_hash_joins_pages_with_newline(tmp_path):
    pdf = tmp_path / "two.pdf"
    _make_pdf(pdf, ["Page one", "Page two"])
    pages = pdf_pages(pdf)
    assert len(pages) == 2
    assert pdf_text_hash(pdf) == text_hash("\n".join(pages)) == text_hash("Page one Page two")


def test_writing_signature_metadata_does_not_change_hash(tmp_path):
    """Issuers render, extract, sign, then write `keywords`; the hash must survive the metadata write."""
    pdf = tmp_path / "statement.pdf"
    _make_pdf(pdf, ["Mock Bank statement", "Salary Credit 62,000.00"])
    before = pdf_text_hash(pdf)

    signed = tmp_path / "statement_signed.pdf"
    with pymupdf.open(pdf) as doc:
        doc.set_metadata({"keywords": json.dumps({"iss": "mock_bank", "sig": "c2lnbmF0dXJl"})})
        doc.save(signed)
    with pymupdf.open(signed) as doc:
        assert json.loads(doc.metadata["keywords"])["iss"] == "mock_bank"
    assert pdf_text_hash(signed) == before

    # an incremental save (append the metadata update to the same file) keeps it too
    with pymupdf.open(pdf) as doc:
        doc.set_metadata({"keywords": json.dumps({"iss": "mock_bank", "sig": "c2lnbmF0dXJl"})})
        doc.saveIncr()
    assert pdf_text_hash(pdf) == before


def test_editing_text_changes_hash(tmp_path):
    a, b = tmp_path / "a.pdf", tmp_path / "b.pdf"
    _make_pdf(a, ["Salary Credit 62,000.00"])
    _make_pdf(b, ["Salary Credit 92,000.00"])
    assert pdf_text_hash(a) != pdf_text_hash(b)
