"""Every cross-module and API shape from CONTRACT.md, as Pydantic v2 models.

Import these; never redefine a shape locally. Section numbers refer to CONTRACT.md.
Shapes CONTRACT leaves open are marked "(scaffold)" and listed in docs/DECISIONS.md.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Field


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# §4 Knowledge model
# ---------------------------------------------------------------------------

EntityType = Literal[
    "PERSON", "PROJECT", "CONCEPT", "DECISION", "ORG", "PLACE", "DOCUMENT", "OBLIGATION", "EVENT"
]
EdgeRel = Literal[
    "WORKS_ON", "PART_OF", "ABOUT", "RELATES_TO", "DECIDED", "LANDLORD_OF", "EMPLOYED_BY",
    "BANKS_WITH", "STUDIED_AT", "PAID", "DUE_ON", "PARTY_TO", "MENTIONED_IN",
]
FactField = Literal[
    "monthly_income", "loan_default_12m", "date_of_birth", "percentage", "result", "board",
    "rent_amount", "agreement_end_date", "id_expiry", "emi_date", "employer", "landlord",
]
SourceType = Literal["issuer_doc", "extracted", "owner_stated"]
Confidence = Literal["high", "low"]
SignatureStatus = Literal["issuer_signed", "unsigned", "invalid"]
DocSource = Literal["pdf", "note", "chat"]  # (scaffold)

OWNER_ENTITY_ID = "e_owner"


class Fact(Model):
    fact_id: str
    entity_id: str
    field: FactField
    value: str
    source_type: SourceType
    doc_id: str | None = None
    quote: str | None = None
    valid_from: str | None = None
    valid_to: str | None = None
    superseded_by: str | None = None
    confidence: Confidence


class FactVersion(Fact):
    """One entry in a field's timeline (scaffold): a Fact plus when it was stored and whether it is current."""

    created_at: str
    current: bool


class Entity(Model):
    entity_id: str
    type: EntityType
    name: str
    attrs: dict[str, str] = Field(default_factory=dict)


class Edge(Model):
    edge_id: str
    src: str
    rel: EdgeRel
    dst: str
    valid_from: str | None = None
    valid_to: str | None = None
    source_chunk_id: str | None = None


class GraphNode(Model):
    id: str
    type: EntityType
    name: str


class GraphEdge(Model):
    id: str
    src: str
    dst: str
    rel: EdgeRel
    valid_from: str | None = None
    valid_to: str | None = None
    source_chunk_id: str | None = None


class Graph(Model):
    nodes: list[GraphNode]
    edges: list[GraphEdge]


class Document(Model):
    doc_id: str
    path: str
    source: DocSource
    doc_type: str | None = None
    signature_status: SignatureStatus
    iss: str | None = None
    text_hash: str | None = None
    ingested_at: str
    removed_at: str | None = None


class Chunk(Model):
    """GET /api/chunks/{chunk_id}. Owner only."""

    chunk_id: str
    doc_id: str
    locator: str
    text: str


class ScoredChunk(Chunk):
    score: float


class SignatureResult(Model):
    """issuer_check.verify_pdf() result (scaffold: status plus issuer and a short reason)."""

    status: SignatureStatus
    iss: str | None = None
    detail: str | None = None


# ---------------------------------------------------------------------------
# Ingestion (§9)
# ---------------------------------------------------------------------------


class IngestResult(Model):
    path: str
    doc_id: str
    source: DocSource
    signature_status: SignatureStatus
    chunks_added: int = 0
    entities_added: int = 0
    facts_added: int = 0


class IngestUploadOut(Model):
    path: str


class IngestSyncOut(Model):
    ingested: list[IngestResult]


class IngestEvent(Model):
    seq: int
    ts: str
    path: str
    doc_id: str
    signature_status: SignatureStatus
    entities_added: int
    facts_added: int


class IngestEvents(Model):
    events: list[IngestEvent]
    last_seq: int


# ---------------------------------------------------------------------------
# Chat (§9, §10)
# ---------------------------------------------------------------------------


class ChatTurn(Model):
    role: Literal["user", "assistant"]
    content: str


class ChatIn(Model):
    question: str
    history: list[ChatTurn] = Field(default_factory=list)


class ChunkRef(Model):
    n: int
    chunk_id: str
    doc_id: str
    locator: str


class Citation(ChunkRef):
    quote: str


class MemoryCandidate(Model):
    candidate_id: str
    statement: str
    kind: Literal["fact", "decision"]
    field: FactField | None = None
    value: str | None = None
    valid_from: str | None = None
    project_entity_id: str | None = None
    status: Literal["pending", "accepted", "discarded"]
    created_at: str


class ChatFinal(Model):
    """Data of the §10 `final` event."""

    answer: str
    citations: list[Citation]
    citation_ok: bool
    flags: list[str] = Field(default_factory=list)
    memory_candidates: list[MemoryCandidate] = Field(default_factory=list)


class ChatResult(ChatFinal):
    """POST /api/chat. The final event plus the entities used (scaffold: merges §10 meta.entities_used)."""

    entities_used: list[str] = Field(default_factory=list)


class ChatMetaData(Model):
    entities_used: list[str]
    chunks: list[ChunkRef]


class ChatTokenData(Model):
    text: str


class ChatDoneData(Model):
    latency_ms: int
    first_token_ms: int


class ChatErrorData(Model):
    message: str


class ChatMetaEvent(Model):
    event: Literal["meta"] = "meta"
    data: ChatMetaData


class ChatTokenEvent(Model):
    event: Literal["token"] = "token"
    data: ChatTokenData


class ChatFinalEvent(Model):
    event: Literal["final"] = "final"
    data: ChatFinal


class ChatDoneEvent(Model):
    event: Literal["done"] = "done"
    data: ChatDoneData


class ChatErrorEvent(Model):
    event: Literal["error"] = "error"
    data: ChatErrorData


ChatEvent = Annotated[
    Union[ChatMetaEvent, ChatTokenEvent, ChatFinalEvent, ChatDoneEvent, ChatErrorEvent],
    Field(discriminator="event"),
]


# ---------------------------------------------------------------------------
# Memory (§9)
# ---------------------------------------------------------------------------


class TeachIn(Model):
    statement: str


class TeachResult(Model):
    fact: Fact
    superseded: list[Fact] = Field(default_factory=list)


class CandidateDecisionIn(Model):
    remember: bool


class CandidateDecisionOut(Model):
    stored: Fact | Entity | None


# ---------------------------------------------------------------------------
# §5 Disclosure model
# ---------------------------------------------------------------------------

ClaimName = Literal["income", "loan_default_12m", "age", "percentage", "result", "board", "unsupported"]
IssuerClaim = Literal[
    "income_ge_25000", "income_ge_50000", "income_ge_75000", "income_ge_100000",
    "loan_default_12m", "age_over_18", "age_over_21",
    "percentage_ge_60", "percentage_ge_75", "percentage_ge_90", "result_pass", "board",
]
AnswerType = Literal["ISSUER_PROOF", "OWNER_ATTESTED", "DECLINED", "CANNOT_CONFIRM", "REFUSED"]
RequestAction = Literal["approve", "answer", "decline", "deny"]
ClaimValue = Union[bool, int, str]


class Claim(Model):
    claim: ClaimName
    op: Literal["ge", "is", "eq"] | None = None
    value: ClaimValue | None = None
    issuer_claim: IssuerClaim | None = None


class Proposal(Model):
    """decide.decide() output (scaffold). `actions` is empty for automatic outcomes."""

    answer_type: AnswerType
    claim: Claim
    result: bool | str | None = None
    favourable: bool | None = None
    actions: list[RequestAction] = Field(default_factory=list)
    reason: str


class ClaimInfo(Model):
    claim: ClaimName
    issuer_provable: list[IssuerClaim]
    favourable: Literal["YES", "NO"] | None


class ClaimsOut(Model):
    """GET /api/claims and MCP list_disclosable_claims. Names only, never values."""

    claims: list[ClaimInfo]


class LedgerCheck(Model):
    allowed: bool
    reason: str | None = None


# ---------------------------------------------------------------------------
# §6 Credentials, presentations, attestations
# ---------------------------------------------------------------------------


class Credential(Model):
    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    iss: str
    credential_type: str
    copy_no: int = Field(alias="copy")  # wire name "copy"; attribute renamed to avoid BaseModel.copy
    holder_pubkey: str
    iat: str
    exp: str
    digests: list[str]
    issuer_sig: str


class Disclosure(Model):
    salt: str
    claim: IssuerClaim
    value: ClaimValue


class Binding(Model):
    nonce: str
    aud: str
    iat: str
    sig: str


class Presentation(Model):
    type: Literal["kavach/presentation"] = "kavach/presentation"
    credential: Credential
    disclosures: list[Disclosure]
    binding: Binding


class Attestation(Model):
    type: Literal["kavach/attestation"] = "kavach/attestation"
    claim: Claim
    answer: bool
    nonce: str
    aud: str
    iat: str
    exp: str
    owner_pairwise_pubkey: str
    sig: str


class CredentialRef(Model):
    """wallet.find_copy() result (scaffold): points at one unused copy."""

    model_config = ConfigDict(extra="forbid", validate_by_name=True, validate_by_alias=True, serialize_by_alias=True)

    cred_id: str
    iss: str
    credential_type: str
    copy_no: int = Field(alias="copy")


class WalletTypeStatus(Model):
    credential_type: str
    iss: str
    unused: int
    total: int


class WalletStatus(Model):
    by_type: list[WalletTypeStatus]
    low: list[str] = Field(default_factory=list)  # credential_types with unused < WALLET_LOW_COPIES (scaffold)


# ---------------------------------------------------------------------------
# Requests, requesters, pairing (§8, §9)
# ---------------------------------------------------------------------------

RequesterStatus = Literal["pending", "paired", "blocked"]
RequestStatus = Literal["pending_pairing", "pending", "done"]
Channel = Literal["web", "mcp"]


class AskIn(Model):
    """POST /api/ask. `sig` covers canonical JSON of all other fields. `ts` is UTC ISO 8601 (scaffold)."""

    requester_pubkey: str
    requester_name: str
    requester_type: str
    question: str
    nonce: str
    ts: str
    sig: str


class AskAck(Model):
    request_id: str
    status: RequestStatus


class AskResult(Model):
    """GET /api/ask/{id}. `payload` is a Presentation or Attestation dict, or null."""

    status: RequestStatus
    answer_type: AnswerType | None = None
    payload: dict[str, Any] | None = None
    owner_pairwise_pubkey: str | None = None


class Requester(Model):
    fingerprint: str
    pubkey: str
    name: str
    type: str
    status: RequesterStatus
    paired_at: str | None = None


class RequestView(Model):
    """Owner-side view of one request (scaffold)."""

    request_id: str
    requester_fp: str
    requester_name: str | None = None
    channel: Channel
    question: str
    claim: Claim | None = None
    proposal: Proposal | None = None
    status: RequestStatus
    answer_type: AnswerType | None = None
    created_at: str
    decided_at: str | None = None


class RequesterDecisionIn(Model):
    approve: bool


class RequestDecisionIn(Model):
    action: RequestAction


# ---------------------------------------------------------------------------
# §11.2 Tools, plans, tasks
# ---------------------------------------------------------------------------

ToolName = Literal["draft_email", "create_reminder", "fill_rental_form", "save_note"]
TaskStatus = Literal["planned", "approved", "rejected", "done", "failed"]


class Attachment(Model):
    type: Literal["presentation"] = "presentation"
    request_id: str


class DraftEmailArgs(Model):
    to: str
    subject: str
    body: str
    attachments: list[Attachment] = Field(default_factory=list)


class CreateReminderArgs(Model):
    title: str
    date: str
    notes: str = ""


class FillRentalFormArgs(Model):
    fields: dict[str, str]


class SaveNoteArgs(Model):
    title: str
    markdown: str


class DraftEmailCall(Model):
    tool: Literal["draft_email"] = "draft_email"
    args: DraftEmailArgs
    preview: str


class CreateReminderCall(Model):
    tool: Literal["create_reminder"] = "create_reminder"
    args: CreateReminderArgs
    preview: str


class FillRentalFormCall(Model):
    tool: Literal["fill_rental_form"] = "fill_rental_form"
    args: FillRentalFormArgs
    preview: str


class SaveNoteCall(Model):
    tool: Literal["save_note"] = "save_note"
    args: SaveNoteArgs
    preview: str


ToolCall = Annotated[
    Union[DraftEmailCall, CreateReminderCall, FillRentalFormCall, SaveNoteCall],
    Field(discriminator="tool"),
]


class Plan(Model):
    instruction: str
    calls: list[ToolCall]
    warnings: list[str] = Field(default_factory=list)


class ToolResult(Model):
    tool: ToolName
    ok: bool
    output_path: str | None = None
    detail: str | None = None


class Task(Model):
    task_id: str
    instruction: str
    plan: Plan
    status: TaskStatus
    result: list[ToolResult] | None = None
    created_at: str
    decided_at: str | None = None


class TaskIn(Model):
    instruction: str


class TaskDecisionIn(Model):
    approve: bool


# ---------------------------------------------------------------------------
# Queue, audit, outbox, health (§9, §13)
# ---------------------------------------------------------------------------


class QueueOut(Model):
    requesters: list[Requester]
    requests: list[RequestView]
    tasks: list[Task]
    wallet: WalletStatus


AuditEvent = Literal[
    "ingested", "document_signature_failed", "requester_pending", "requester_paired",
    "requester_blocked", "request_received", "request_auto_refused", "request_cannot_confirm",
    "request_refused_ledger", "disclosure_answered", "disclosure_declined", "disclosure_denied",
    "memory_taught", "memory_candidate_accepted", "task_planned", "task_approved", "task_rejected",
    "task_executed", "task_failed", "wallet_low",
]


class AuditEntry(Model):
    seq: int
    ts: str
    event: AuditEvent
    ref_id: str | None = None
    detail: dict[str, Any]
    prev_hash: str
    entry_hash: str


class ChainStatus(Model):
    intact: bool
    broken_at: int | None = None
    entries: int = 0


class AuditOut(Model):
    entries: list[AuditEntry]
    chain_intact: bool
    broken_at: int | None = None


class OutboxItem(Model):
    name: str
    kind: Literal["eml", "ics", "pdf"]
    size: int
    created_at: str


class Health(Model):
    """GET /api/health (scaffold: `models` maps role -> configured model name)."""

    ollama: bool
    models: dict[str, str]
    model_loaded: bool
    db: bool
    vault_dir: str


# ---------------------------------------------------------------------------
# §6.4 Verifier and §12 requester backend
# ---------------------------------------------------------------------------


class VerifierCheck(Model):
    name: str
    ok: bool
    detail: str = ""


class VerifierOutput(Model):
    """`claim` is the disclosed issuer claim name, or the attested claim rendered as text (scaffold)."""

    answer_type: AnswerType
    claim: str
    result: bool | str | None = None
    checks: list[VerifierCheck]
    all_ok: bool


class RIdentity(Model):
    name: str
    type: str
    fingerprint: str
    owner_url: str


class RAskIn(Model):
    question: str


class RAskOut(Model):
    local_id: str
    request_id: str
    status: RequestStatus


class RRequest(Model):
    local_id: str
    request_id: str
    question: str
    status: RequestStatus
    via: Literal["web", "agent"]
    result: VerifierOutput | None = None


class RStorageFile(Model):
    name: str
    size: int
    preview_json: str


class RStorage(Model):
    files: list[RStorageFile]
