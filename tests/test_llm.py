import json

import httpx
import numpy as np
import pytest

from kavach import config
from kavach.brain import llm
from kavach.models import Claim

MSGS = [{"role": "user", "content": "q"}]


def test_structured_request_disables_thinking_and_sets_schema():
    body = llm.structured_request(MSGS, Claim)
    assert body["think"] is False
    assert body["format"] == Claim.model_json_schema()
    assert body["stream"] is False and body["options"]["temperature"] == 0
    assert body["options"]["num_ctx"] == config.NUM_CTX
    assert body["model"] == config.FAST_MODEL and body["keep_alive"] == config.OLLAMA_KEEP_ALIVE


def test_embed_request_is_one_batched_list():
    body = llm.embed_request(["a", "b", "c"])
    assert body["input"] == ["a", "b", "c"] and body["model"] == config.EMBED_MODEL
    assert llm.EMBED_PATH == "/api/embed"


def test_chat_request_defaults_to_llm_model():
    assert llm.chat_request([], stream=True)["model"] == config.LLM_MODEL


# ---------------------------------------------------------------------------
# HTTP behaviour against a mock Ollama
# ---------------------------------------------------------------------------

class FakeOllama:
    """Records every request; `replies` is a list of (status, body) or callables returning httpx.Response."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, body = reply
        if isinstance(body, (dict, list)):
            return httpx.Response(status, json=body)
        return httpx.Response(status, content=body)

    def bodies(self) -> list[dict]:
        return [json.loads(r.content) for r in self.requests]


@pytest.fixture
def ollama(monkeypatch):
    def install(*replies):
        fake = FakeOllama(replies)
        client = httpx.Client(base_url=config.OLLAMA_URL, transport=httpx.MockTransport(fake), timeout=llm.TIMEOUT)
        monkeypatch.setattr(llm, "_http", client)
        return fake
    return install


def _chat_reply(content: str) -> tuple[int, dict]:
    return 200, {"model": "m", "message": {"role": "assistant", "content": content}, "done": True}


def _ndjson(*objs) -> bytes:
    return b"".join(json.dumps(o).encode() + b"\n" for o in objs)


def _assert_chat_options(body: dict) -> None:
    assert body["keep_alive"] == config.OLLAMA_KEEP_ALIVE
    assert body["options"] == {"temperature": 0, "num_ctx": config.NUM_CTX}


def test_chat_sends_builder_body_and_returns_content(ollama):
    fake = ollama(_chat_reply("hello"))
    assert llm.chat(MSGS) == "hello"
    (req,) = fake.requests
    assert req.url.path == llm.CHAT_PATH
    body = fake.bodies()[0]
    assert body == llm.chat_request(MSGS, stream=False)
    _assert_chat_options(body)
    assert body["model"] == config.LLM_MODEL and body["stream"] is False


def test_chat_stream_yields_pieces_until_done(ollama):
    stream = _ndjson({"message": {"content": "Hel"}, "done": False}, {"message": {"content": ""}, "done": False},
                     {"message": {"content": "lo"}, "done": False}, {"message": {"content": ""}, "done": True},
                     {"message": {"content": "after done"}, "done": False})
    fake = ollama((200, stream))
    assert list(llm.chat_stream(MSGS)) == ["Hel", "lo"]
    body = fake.bodies()[0]
    assert body["stream"] is True
    _assert_chat_options(body)
    timeout = fake.requests[0].extensions["timeout"]
    assert timeout["read"] == 120.0 and timeout["connect"] == 5.0


def test_chat_stream_reports_final_stats(ollama):
    ollama((200, _ndjson({"message": {"content": "Hi"}, "done": False},
                         {"message": {"content": ""}, "done": True, "prompt_eval_count": 1540,
                          "prompt_eval_duration": 52_000_000_000, "eval_count": 23, "model": "x"})))
    stats: dict[str, int] = {}
    assert list(llm.chat_stream(MSGS, stats=stats)) == ["Hi"]
    assert stats == {"prompt_eval_count": 1540, "prompt_eval_duration": 52_000_000_000, "eval_count": 23}


def test_chat_stream_error_line_raises(ollama):
    ollama((200, _ndjson({"message": {"content": "partial"}, "done": False}, {"error": "model crashed"})))
    gen = llm.chat_stream(MSGS)
    assert next(gen) == "partial"
    with pytest.raises(llm.LLMError, match="model crashed"):
        next(gen)


def test_chat_stream_http_error_and_truncation_raise(ollama):
    ollama((404, {"error": "model not found"}))
    with pytest.raises(llm.LLMError, match="404"):
        list(llm.chat_stream(MSGS))
    ollama((200, _ndjson({"message": {"content": "cut"}, "done": False})))
    with pytest.raises(llm.LLMError, match="without done"):
        list(llm.chat_stream(MSGS))


def test_structured_returns_validated_model(ollama):
    fake = ollama(_chat_reply('{"claim": "income", "op": "ge", "value": 50000}'))
    assert llm.structured(MSGS, Claim) == Claim(claim="income", op="ge", value=50000)
    body = fake.bodies()[0]
    assert body == llm.structured_request(MSGS, Claim)
    _assert_chat_options(body)
    assert body["think"] is False and body["format"] == Claim.model_json_schema()
    assert fake.requests[0].extensions["timeout"]["read"] == 300.0


def test_structured_retry_appends_validation_error(ollama):
    fake = ollama(_chat_reply('{"claim": "salary"}'), _chat_reply('{"claim": "age", "op": "ge", "value": 18}'))
    assert llm.structured(MSGS, Claim).claim == "age"
    first, second = fake.bodies()
    assert first["messages"] == MSGS
    assert second["messages"][:-2] == MSGS
    assert second["messages"][-2] == {"role": "assistant", "content": '{"claim": "salary"}'}
    note = second["messages"][-1]
    assert note["role"] == "user" and note["content"].startswith("Your previous reply was invalid: ")
    assert "claim" in note["content"] and note["content"].endswith("Return only JSON matching the schema.")
    _assert_chat_options(second)


def test_structured_invalid_json_then_valid(ollama):
    fake = ollama(_chat_reply("not json"), _chat_reply('{"claim": "unsupported"}'))
    assert llm.structured(MSGS, Claim).claim == "unsupported"
    assert "invalid" in fake.bodies()[1]["messages"][-1]["content"]


def test_structured_raises_after_two_bad_replies(ollama):
    fake = ollama(_chat_reply('{"claim": 1}'), _chat_reply('{"claim": 2}'))
    with pytest.raises(llm.LLMError, match="Claim failed after retry"):
        llm.structured(MSGS, Claim)
    assert len(fake.requests) == 2


def test_structured_retries_transport_error_with_same_messages(ollama):
    fake = ollama((500, {"error": "busy"}), _chat_reply('{"claim": "board"}'))
    assert llm.structured(MSGS, Claim).claim == "board"
    assert fake.bodies()[1]["messages"] == MSGS


def test_embed_returns_float32_matrix(ollama):
    vecs = [[0.1] * config.EMBED_DIM, [0.2] * config.EMBED_DIM]
    fake = ollama((200, {"embeddings": vecs}))
    out = llm.embed(["a", "b"])
    assert out.dtype == np.float32 and out.shape == (2, config.EMBED_DIM)
    (req,) = fake.requests
    assert req.url.path == llm.EMBED_PATH
    body = fake.bodies()[0]
    assert body == llm.embed_request(["a", "b"])
    assert body["keep_alive"] == config.OLLAMA_KEEP_ALIVE and body["input"] == ["a", "b"]


def test_embed_empty_makes_no_call(ollama):
    fake = ollama()
    out = llm.embed([])
    assert out.shape == (0, config.EMBED_DIM) and out.dtype == np.float32
    assert fake.requests == []


@pytest.mark.parametrize("embeddings", [
    [[0.1] * config.EMBED_DIM],  # one vector for two texts
    [[0.1] * config.EMBED_DIM, [0.1] * 10],  # ragged
    [[0.1] * 10, [0.1] * 10],  # wrong dim
])
def test_embed_bad_shapes_raise(ollama, embeddings):
    ollama((200, {"embeddings": embeddings}))
    with pytest.raises(llm.LLMError):
        llm.embed(["a", "b"])


def test_connection_refused_becomes_llm_error(ollama):
    ollama(httpx.ConnectError("refused"))
    with pytest.raises(llm.LLMError, match="refused"):
        llm.chat(MSGS)


def test_ollama_error_body_becomes_llm_error(ollama):
    ollama((200, {"error": "out of memory"}))
    with pytest.raises(llm.LLMError, match="out of memory"):
        llm.chat(MSGS)


# ---------------------------------------------------------------------------
# Real Ollama
# ---------------------------------------------------------------------------

@pytest.fixture
def real_ollama(monkeypatch):
    monkeypatch.setattr(llm, "_http", None)
    try:
        httpx.get(f"{config.OLLAMA_URL}/api/tags", timeout=2.0).raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"Ollama not reachable at {config.OLLAMA_URL}")
    yield
    if llm._http is not None:
        llm._http.close()


@pytest.mark.llm
def test_real_chat(real_ollama):
    reply = llm.chat([{"role": "user", "content": "Reply with exactly the word: pong"}], config.FAST_MODEL)
    assert "pong" in reply.lower()


@pytest.mark.llm
def test_real_chat_stream(real_ollama):
    pieces = list(llm.chat_stream([{"role": "user", "content": "Count from 1 to 5, separated by spaces."}]))
    assert len(pieces) > 1 and "3" in "".join(pieces)


@pytest.mark.llm
def test_real_structured_claim(real_ollama):
    msgs = [{"role": "system", "content": "Map the question to a claim object. claim is one of income, loan_default_12m, "
                                          "age, percentage, result, board, unsupported. op is ge for 'at least' "
                                          "thresholds. value is the threshold as an integer."},
            {"role": "user", "content": "Is your monthly income at least 50000?"}]
    claim = llm.structured(msgs, Claim)
    assert claim.claim == "income" and claim.value == 50000


@pytest.mark.llm
def test_real_embed_similarity(real_ollama):
    out = llm.embed(["monthly rent for the flat", "the apartment rent each month", "photosynthesis in leaves"])
    assert out.shape == (3, config.EMBED_DIM) and out.dtype == np.float32
    unit = out / np.linalg.norm(out, axis=1, keepdims=True)
    assert unit[0] @ unit[1] > unit[0] @ unit[2]


@pytest.mark.llm
def test_real_long_context_keeps_start(real_ollama):
    """~5k-token prompt (qwen tokenises digits one by one: ~24 tokens per line). Without num_ctx Ollama's
    default context truncated this to ~2k tokens and the model invented a passphrase."""
    assert config.NUM_CTX >= 8192
    filler = "\n".join(f"Ledger line {i}: warehouse {i % 17} received {i * 7} crates of item code Q{i * 31 % 997}."
                       for i in range(200))
    prompt = ("The secret passphrase is TANGERINE-4471. Remember it.\n\n" + filler +
              "\n\nWhat is the secret passphrase given at the very beginning? Reply with only the passphrase.")
    assert len(prompt) > 12000
    reply = llm.chat([{"role": "user", "content": prompt}], config.FAST_MODEL)
    assert "TANGERINE-4471" in reply.upper()
