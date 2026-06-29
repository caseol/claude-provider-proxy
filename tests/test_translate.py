"""Offline unit tests for the translation core + provider config (no network)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_provider_proxy import translate_openai as tx          # noqa: E402
from claude_provider_proxy import proxy_core                      # noqa: E402
from claude_provider_proxy.providers import load_providers, ProviderConfig  # noqa: E402

ZEN = load_providers()["opencode-zen"]


# ---- request translation ----

def test_system_string_and_messages():
    body = {"model": "m", "system": "be brief",
            "messages": [{"role": "user", "content": "hi"}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0] == {"role": "system", "content": "be brief"}
    assert o["messages"][1] == {"role": "user", "content": "hi"}
    assert o["stream"] is False


def test_system_as_block_list_is_flattened():
    body = {"model": "m",
            "system": [{"type": "text", "text": "A", "cache_control": {"type": "ephemeral"}},
                       {"type": "text", "text": "B"}],
            "messages": [{"role": "user", "content": "x"}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "A\nB"   # cache_control dropped


def test_content_blocks_flattened_with_markers():
    body = {"model": "m", "messages": [{"role": "assistant", "content": [
        {"type": "text", "text": "ok"},
        {"type": "tool_use", "name": "ls", "id": "t1", "input": {"path": "/"}},
    ]}]}
    o = tx.anthropic_to_openai(body, ZEN)
    c = o["messages"][0]["content"]
    assert "ok" in c and "[tool_use: ls id=t1 input=" in c


def test_reasoning_token_floor():
    body = {"model": "deepseek-v4-flash", "max_tokens": 10,
            "messages": [{"role": "user", "content": "x"}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["max_tokens"] == 1024   # bumped for reasoning model


def test_tools_and_stop_translation():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
            "stop_sequences": ["END"]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["tools"][0]["function"]["name"] == "f"
    assert o["stop"] == ["END"]


# ---- response translation ----

def test_openai_response_text_and_tools():
    data = {"id": "x", "choices": [{"finish_reason": "tool_calls", "message": {
        "content": "hello",
        "tool_calls": [{"id": "tc1", "function": {"name": "f", "arguments": '{"a":1}'}}]}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 7}}
    a = tx.openai_to_anthropic_response(data, "m")
    kinds = [b["type"] for b in a["content"]]
    assert "text" in kinds and "tool_use" in kinds
    assert a["stop_reason"] == "tool_use"
    assert a["usage"] == {"input_tokens": 5, "output_tokens": 7}


def test_embedded_tool_marker_parsing():
    text = 'do this [tool_use: ls id=t1 input={"path": "/x"}] done'
    blocks = tx.split_text_and_tools(text)
    assert blocks is not None
    assert blocks[0]["type"] == "text"
    tu = [b for b in blocks if b["type"] == "tool_use"][0]
    assert tu["name"] == "ls" and tu["input"] == {"path": "/x"}


def test_map_stop_reason():
    assert tx.map_stop_reason("length") == "max_tokens"
    assert tx.map_stop_reason("stop") == "end_turn"
    assert tx.map_stop_reason(None) == "end_turn"


# ---- provider config / fallback ----

def test_chain_for():
    p = ProviderConfig(name="x", flavor="openai", base_url="http://u", api_key_env="K",
                       fallbacks={"a": ["b", "c"]}, default_fallback=["z"])
    assert p.chain_for("a") == ["a", "b", "c"]
    assert p.chain_for("q") == ["q", "z"]


def test_cache_control_strip_rule():
    p = ProviderConfig(name="go", flavor="anthropic", base_url="http://u",
                       api_key_env="K", cache_control_strip=["kimi"])
    assert p.strip_cache_control_for("kimi-k2.7-code") is True
    assert p.strip_cache_control_for("deepseek-v4-flash") is False


def test_strip_cache_control_recursive():
    body = {"model": "kimi", "messages": [{"role": "user",
            "content": [{"type": "text", "text": "x", "cache_control": {"type": "ephemeral"}}]}],
            "system": [{"type": "text", "text": "s", "cache_control": {"x": 1}}]}
    proxy_core.strip_cache_control(body)
    assert "cache_control" not in body["messages"][0]["content"][0]
    assert "cache_control" not in body["system"][0]


# ---- streaming ----

def test_streaming_events_and_usage():
    import asyncio

    async def fake_lines():
        chunks = [
            {"choices": [{"delta": {"content": "Hel"}}]},
            {"choices": [{"delta": {"content": "lo"}}]},
            {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"completion_tokens": 2}},
        ]
        for c in chunks:
            yield "data: " + json.dumps(c)
        yield "data: [DONE]"

    async def run():
        return [e async for e in tx.stream_anthropic_events(fake_lines(), "m")]

    events = asyncio.run(run())
    joined = "".join(events)
    assert "message_start" in joined
    assert '"text_delta"' in joined and "Hel" in joined and "lo" in joined
    assert '"output_tokens": 2' in joined   # usage propagated (fix)
    assert events[-1].startswith("event: message_stop")
