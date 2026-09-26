"""Credential copies and their one-time holder keys. Private keys never leave the wallet."""

from __future__ import annotations

from pathlib import Path

from kavach.models import CredentialRef, WalletStatus, WalletTypeStatus


def find_copy(issuer_claim: str) -> CredentialRef | None:
    """Stub: no credentials yet."""
    return None


def status() -> WalletStatus:
    """Stub: canned counts."""
    return WalletStatus(by_type=[
        WalletTypeStatus(credential_type="income_proof", iss="mock_bank", unused=17, total=20),
        WalletTypeStatus(credential_type="marksheet", iss="mock_board", unused=20, total=20),
        WalletTypeStatus(credential_type="id_card", iss="mock_govt", unused=2, total=20),
    ], low=["id_card"])


def export_holder_pubkeys(n: int = 20) -> Path:
    """Stub: will write n fresh holder public keys to pubkeys.json for an issuer (CONTRACT §6.1)."""
    raise NotImplementedError("TRUST step 1")


def import_batch(path: Path) -> int:
    """Stub: will import an issued batch and return the number of copies stored."""
    raise NotImplementedError("TRUST step 1")
