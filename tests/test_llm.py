from kavach import config
from kavach.brain import llm
from kavach.models import Claim


def test_structured_request_disables_thinking_and_sets_schema():
    body = llm.structured_request([{"role": "user", "content": "q"}], Claim)
    assert body["think"] is False
    assert body["format"] == Claim.model_json_schema()
    assert body["stream"] is False and body["options"]["temperature"] == 0
    assert body["model"] == config.FAST_MODEL and body["keep_alive"] == config.OLLAMA_KEEP_ALIVE


def test_embed_request_is_one_batched_list():
    body = llm.embed_request(["a", "b", "c"])
    assert body["input"] == ["a", "b", "c"] and body["model"] == config.EMBED_MODEL
    assert llm.EMBED_PATH == "/api/embed"


def test_chat_request_defaults_to_llm_model():
    assert llm.chat_request([], stream=True)["model"] == config.LLM_MODEL
