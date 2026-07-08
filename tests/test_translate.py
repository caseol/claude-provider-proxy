"""Offline unit tests for the translation core + provider config (no network)."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx                                                       # noqa: E402

from claude_provider_proxy import translate_openai as tx          # noqa: E402
from claude_provider_proxy import proxy_core                      # noqa: E402
from claude_provider_proxy.providers import load_providers, ProviderConfig  # noqa: E402

ZEN = load_providers()["opencode-zen"]
NATIVE = ProviderConfig(name="nt", flavor="openai", base_url="http://u", api_key_env="K",
                        native_tool_history=True)


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


def test_temperature_only_when_provided():
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert "temperature" not in tx.anthropic_to_openai(base, ZEN)
    assert tx.anthropic_to_openai({**base, "temperature": 1}, ZEN)["temperature"] == 1


def test_reasoning_token_floor():
    body = {"model": "deepseek-v4-flash", "max_tokens": 10,
            "messages": [{"role": "user", "content": "x"}]}
    o = tx.anthropic_to_openai(body, ZEN)
    # bumped to the provider's floor (zen: 4096 — free deepseek always reasons)
    assert o["max_tokens"] == ZEN.min_tokens_reasoning == 4096


def test_tools_and_stop_translation():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "f", "description": "d", "input_schema": {"type": "object"}}],
            "stop_sequences": ["END"]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["tools"][0]["function"]["name"] == "f"
    assert o["stop"] == ["END"]


def test_thinking_block_preserves_text():
    body = {"model": "m", "messages": [{"role": "assistant", "content": [
        {"type": "thinking", "thinking": "let me think..."},
    ]}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "[thinking: let me think...]"


def test_redacted_thinking_block_placeholder():
    body = {"model": "m", "messages": [{"role": "assistant", "content": [
        {"type": "redacted_thinking", "data": "opaque-blob"},
    ]}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "[redacted_thinking: opaque-blob]"


def test_document_block_placeholder_with_title():
    body = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "document", "title": "invoice.pdf",
         "source": {"media_type": "application/pdf", "type": "base64"}},
    ]}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "[document: invoice.pdf]"


def test_document_block_placeholder_media_type_fallback():
    body = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "document", "source": {"media_type": "application/pdf"}},
    ]}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "[document: application/pdf]"


def test_document_block_placeholder_missing_fields():
    body = {"model": "m", "messages": [{"role": "user", "content": [{"type": "document"}]}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "[document: unknown]"


def test_unknown_block_type_still_falls_through():
    body = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "some_future_block", "foo": "bar"},
    ]}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert "some_future_block" in o["messages"][0]["content"]


def test_flatten_system_preserves_nontext_blocks():
    """Regression: non-text system blocks used to vanish silently (worse than the
    legacy nv-proxy, which at least stringified them)."""
    body = {"model": "m",
            "system": [{"type": "text", "text": "A"},
                       {"type": "document", "title": "spec.pdf"}],
            "messages": [{"role": "user", "content": "x"}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "A\n[document: spec.pdf]"


def test_empty_content_gets_placeholder():
    body = {"model": "m", "messages": [{"role": "assistant", "content": []}]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert o["messages"][0]["content"] == "[empty message]"


def test_tool_choice_auto_any_specific():
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "grep", "description": "d", "input_schema": {"type": "object"}}]}
    assert tx.anthropic_to_openai({**base, "tool_choice": {"type": "auto"}}, ZEN)["tool_choice"] == "auto"
    assert tx.anthropic_to_openai({**base, "tool_choice": {"type": "any"}}, ZEN)["tool_choice"] == "required"
    specific = tx.anthropic_to_openai(
        {**base, "tool_choice": {"type": "tool", "name": "grep"}}, ZEN)["tool_choice"]
    assert specific == {"type": "function", "function": {"name": "grep"}}


def test_tool_choice_absent_or_unrecognized_is_omitted():
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [{"name": "grep", "description": "d", "input_schema": {"type": "object"}}]}
    assert "tool_choice" not in tx.anthropic_to_openai(base, ZEN)
    weird = {**base, "tool_choice": {"type": "something_new"}}
    assert "tool_choice" not in tx.anthropic_to_openai(weird, ZEN)


def test_empty_tools_array_is_omitted():
    """Claude Code sends tools: [] on tool-less background calls; NIM 400s on an
    empty tools array, so the field (and any tool_choice) must be dropped."""
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "tools": [], "tool_choice": {"type": "auto"}}
    o = tx.anthropic_to_openai(body, ZEN)
    assert "tools" not in o and "tool_choice" not in o


def test_top_p_passthrough():
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert "top_p" not in tx.anthropic_to_openai(base, ZEN)
    assert tx.anthropic_to_openai({**base, "top_p": 0.9}, ZEN)["top_p"] == 0.9


def test_top_k_metadata_parallel_tool_calls_not_forwarded():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "top_k": 40, "metadata": {"user_id": "abc"}, "parallel_tool_calls": False}
    o = tx.anthropic_to_openai(body, ZEN)
    assert "top_k" not in o and "metadata" not in o and "parallel_tool_calls" not in o


# ---- native tool history (native_tool_history=True) ----

def test_native_tool_history_full_round_trip():
    """Full turn (user -> assistant tool_use -> user tool_result) round-trips to
    OpenAI's native assistant.tool_calls + role:"tool" shape, with the tool reply
    ordered immediately after the assistant tool_calls message."""
    body = {"model": "m", "messages": [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "ok, listing"},
            {"type": "tool_use", "id": "call_1", "name": "ls", "input": {"path": "/x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1",
             "content": [{"type": "text", "text": "a.txt"}]},
            {"type": "text", "text": "now what?"},
        ]},
    ]}
    o = tx.anthropic_to_openai(body, NATIVE)
    msgs = o["messages"]
    assert msgs[0] == {"role": "user", "content": "list files"}
    assert msgs[1]["role"] == "assistant"
    assert msgs[1]["content"] == "ok, listing"
    tc = msgs[1]["tool_calls"][0]
    assert tc["id"] == "call_1"
    assert tc["function"]["name"] == "ls"
    assert json.loads(tc["function"]["arguments"]) == {"path": "/x"}
    assert msgs[2] == {"role": "tool", "tool_call_id": "call_1", "content": "a.txt"}
    assert msgs[3] == {"role": "user", "content": "now what?"}


def test_native_tool_history_assistant_tool_only_has_null_content():
    body = {"model": "m", "messages": [{"role": "assistant", "content": [
        {"type": "tool_use", "id": "t1", "name": "ls", "input": {}},
    ]}]}
    o = tx.anthropic_to_openai(body, NATIVE)
    m = o["messages"][0]
    assert m["content"] is None
    assert m["tool_calls"][0]["function"]["name"] == "ls"


def test_native_tool_history_error_result_prefixed():
    body = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "bad", "is_error": True},
    ]}]}
    o = tx.anthropic_to_openai(body, NATIVE)
    assert o["messages"][0]["content"].startswith("[tool error] ")


def test_native_tool_history_empty_result_placeholder():
    body = {"model": "m", "messages": [{"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": []},
    ]}]}
    o = tx.anthropic_to_openai(body, NATIVE)
    assert o["messages"][0]["content"] == "[empty result]"


def test_marker_mode_unchanged_by_default():
    """Same round-trip as test_native_tool_history_full_round_trip, but on a provider
    without native_tool_history: markers stay the default, no role:"tool"/tool_calls."""
    body = {"model": "m", "messages": [
        {"role": "user", "content": "list files"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "ok, listing"},
            {"type": "tool_use", "id": "call_1", "name": "ls", "input": {"path": "/x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "call_1",
             "content": [{"type": "text", "text": "a.txt"}]},
            {"type": "text", "text": "now what?"},
        ]},
    ]}
    provider = ProviderConfig(name="p", flavor="openai", base_url="http://u", api_key_env="K")
    o = tx.anthropic_to_openai(body, provider)
    assert not any(m["role"] == "tool" for m in o["messages"])
    assert not any("tool_calls" in m for m in o["messages"])
    assert any("[tool_result:" in m["content"] for m in o["messages"])
    assert any("[tool_use:" in m["content"] for m in o["messages"])


def test_merge_consecutive_user_messages():
    body = {"model": "m", "messages": [
        {"role": "user", "content": "a"},
        {"role": "user", "content": "b"},
    ]}
    o = tx.anthropic_to_openai(body, ZEN)
    assert len(o["messages"]) == 1
    assert o["messages"][0] == {"role": "user", "content": "a\nb"}


def test_merge_does_not_cross_tool_calls():
    """An assistant tool_calls message and an adjacent plain assistant text message
    must not merge (tool_calls carriers are never merge targets). Likewise, two
    adjacent role:"tool" messages (distinct results) stay separate, not merged."""
    msgs = [
        {"role": "assistant", "content": [
            {"type": "tool_use", "id": "t1", "name": "ls", "input": {}}]},
        {"role": "assistant", "content": "done"},
    ]
    out = tx.convert_messages(msgs, native_tools=True)
    assert len(out) == 2
    assert "tool_calls" in out[0]
    assert out[1] == {"role": "assistant", "content": "done"}

    tool_msgs = [
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "a", "content": "x"}]},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "b", "content": "y"}]},
    ]
    out2 = tx.convert_messages(tool_msgs, native_tools=True)
    assert out2 == [
        {"role": "tool", "tool_call_id": "a", "content": "x"},
        {"role": "tool", "tool_call_id": "b", "content": "y"},
    ]


# ---- reasoning_extra_body injection ----

def test_reasoning_extra_body_injected_when_thinking_enabled():
    provider = ProviderConfig(name="r", flavor="openai", base_url="http://u", api_key_env="K",
                              reasoning_models={"m"},
                              reasoning_extra_body={"chat_template_kwargs": {"thinking": True}})
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}

    enabled = {**base, "thinking": {"type": "enabled", "budget_tokens": 1024}}
    assert tx.anthropic_to_openai(enabled, provider)["chat_template_kwargs"] == {"thinking": True}

    assert "chat_template_kwargs" not in tx.anthropic_to_openai(base, provider)

    other_model = {**enabled, "model": "other"}
    assert "chat_template_kwargs" not in tx.anthropic_to_openai(other_model, provider)

    disabled = {**base, "thinking": {"type": "disabled"}}
    assert "chat_template_kwargs" not in tx.anthropic_to_openai(disabled, provider)


def test_reasoning_extra_body_not_mutated():
    """out.update(deepcopy(...)) must not let the caller's mutation of the returned
    request dict leak back into the provider's shared reasoning_extra_body."""
    provider = ProviderConfig(name="r", flavor="openai", base_url="http://u", api_key_env="K",
                              reasoning_models={"m"},
                              reasoning_extra_body={"chat_template_kwargs": {"thinking": True}})
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "thinking": {"type": "enabled", "budget_tokens": 1024}}

    o1 = tx.anthropic_to_openai(body, provider)
    o1["chat_template_kwargs"]["thinking"] = False
    o1["chat_template_kwargs"]["new_key"] = "oops"

    assert provider.reasoning_extra_body == {"chat_template_kwargs": {"thinking": True}}

    o2 = tx.anthropic_to_openai(body, provider)
    assert o2["chat_template_kwargs"] == {"thinking": True}


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


def test_malformed_marker_is_skipped_not_fatal_to_the_whole_scan():
    """Regression: a model can garble one marker's header (e.g. emitting "params="
    instead of "input=", observed from nemotron-3-ultra-free on a complex multi-line
    command) — a single malformed occurrence must not blind the parser to a
    well-formed marker elsewhere in the same text."""
    text = ('junk [tool_use: Bash id="x"="Bash" params={"command": "a"}] middle '
            '[tool_use: ls id=t2 input={"path": "/x"}] tail')
    blocks = tx.find_tool_use_blocks(text)
    assert len(blocks) == 1
    assert blocks[0]["name"] == "ls" and blocks[0]["id"] == "t2"
    assert blocks[0]["input"] == {"path": "/x"}


def test_malformed_marker_only_returns_no_blocks():
    text = 'oops [tool_use: Bash id="x"="Bash" params={"command": "a"}] done'
    assert tx.find_tool_use_blocks(text) == []
    assert tx.split_text_and_tools(text) is None


def test_unparsed_marker_logs_warning(caplog):
    """A malformed marker that never resolves into a tool_use block must at least
    leave a trace in the daemon logs — previously this failure was 100% silent
    server-side and only discoverable by reading the Claude Code session transcript."""
    import logging

    data = {"id": "x", "choices": [{"finish_reason": "stop", "message": {
        "content": 'oops [tool_use: Bash id="x"="Bash" params={"command": "a"}] done'}}],
        "usage": {}}
    with caplog.at_level(logging.WARNING, logger="claude_provider_proxy"):
        result = tx.openai_to_anthropic_response(data, "nemotron-3-ultra-free")
    assert result["content"][0]["type"] == "text"
    assert any("failed to parse" in r.message for r in caplog.records)


def test_well_formed_marker_does_not_log_warning(caplog):
    import logging

    data = {"id": "x", "choices": [{"finish_reason": "stop", "message": {
        "content": 'do this [tool_use: ls id=t1 input={"path": "/x"}] done'}}],
        "usage": {}}
    with caplog.at_level(logging.WARNING, logger="claude_provider_proxy"):
        tx.openai_to_anthropic_response(data, "m")
    assert not any("failed to parse" in r.message for r in caplog.records)


def test_marker_with_malformed_json_body_logs_and_uses_empty_input(caplog):
    """Header parses fine (name/id found) but the brace-matched input body itself is
    not valid JSON (e.g. a duplicated key, as observed from nemotron-3-ultra-free) —
    previously silently became {} with zero trace in the logs."""
    import logging

    text = ('[tool_use: Bash id=t1 input={"description": "description": "x"}] done')
    with caplog.at_level(logging.WARNING, logger="claude_provider_proxy"):
        blocks = tx.find_tool_use_blocks(text, model="nemotron-3-ultra-free")
    assert len(blocks) == 1
    assert blocks[0]["input"] == {}
    assert any("malformed JSON input" in r.message for r in caplog.records)


def test_duplicate_marker_ids_get_deduped(caplog):
    """Two distinct markers sharing the same id (observed as hallucinated repetition
    from weak models) must not produce two tool_use blocks with the same id — that
    would corrupt the client's tool_use -> tool_result correlation."""
    import logging

    text = ('[tool_use: Bash id=t1 input={"command": "a"}] then '
            '[tool_use: Bash id=t1 input={"command": "b"}] end')
    with caplog.at_level(logging.WARNING, logger="claude_provider_proxy"):
        blocks = tx.split_text_and_tools(text, model="kimi-k2.7-code")
    tool_blocks = [b for b in blocks if b["type"] == "tool_use"]
    assert len(tool_blocks) == 2
    assert tool_blocks[0]["id"] != tool_blocks[1]["id"]
    assert any("reused tool_use id" in r.message for r in caplog.records)


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


def test_zen_has_a_universal_fallback():
    """Regression: opencode-zen had no fallbacks/default_fallback at all, so any of
    its free-tier models not explicitly listed (nemotron-3-ultra-free, hy3-free,
    mimo-v2.5-free, north-mini-code-free, ...) had zero safety net — a single
    retryable error ended the turn outright instead of advancing to a stable model."""
    assert ZEN.chain_for("nemotron-3-ultra-free") == ["nemotron-3-ultra-free",
                                                       "deepseek-v4-flash-free"]


def test_load_providers_ignores_comment_key(tmp_path, monkeypatch):
    """providers.example.json (and the docs) use a top-level "_comment" string for
    guidance; load_providers() must not choke on it (regression: used to insert a
    bogus {} entry that then crashed _make() with KeyError('base_url'))."""
    import claude_provider_proxy.providers as providers_mod

    cfg = tmp_path / "providers.json"
    cfg.write_text(json.dumps({
        "_comment": "human note, not a provider",
        "opencode-go": {"fallbacks": {"qwen3.7-max": ["kimi-k2.7-code"]}},
    }))
    monkeypatch.setattr(providers_mod, "PROVIDERS_FILE", cfg)

    result = providers_mod.load_providers()

    assert "_comment" not in result
    assert result["opencode-go"].fallbacks == {"qwen3.7-max": ["kimi-k2.7-code"]}
    assert result["opencode-go"].base_url  # inherited from BUILTIN, not clobbered


def test_cache_control_strip_rule():
    p = ProviderConfig(name="go", flavor="anthropic", base_url="http://u",
                       api_key_env="K", cache_control_strip=["kimi"])
    assert p.strip_cache_control_for("kimi-k2.7-code") is True
    assert p.strip_cache_control_for("deepseek-v4-flash") is False


def test_matches_transient_pattern_builtin_opencode_go():
    go = load_providers()["opencode-go"]
    body = ('{"error":{"message":"Error from provider (Console Go): '
            'Upstream request failed","type":"invalid_request_error"}}')
    assert go.matches_transient_pattern(body) is True
    assert go.matches_transient_pattern('{"error":{"message":"model not supported"}}') is False


def test_matches_transient_pattern_default_empty():
    """Providers without an explicit transient_error_patterns override (e.g. zen,
    nvidia) never treat a 400 as retryable, matching pre-incident behavior."""
    assert ZEN.transient_error_patterns == []
    assert ZEN.matches_transient_pattern("Upstream request failed") is False


def test_normalize_content_string_to_blocks():
    body = {"messages": [{"role": "user", "content": "hi"},
                         {"role": "assistant", "content": [{"type": "text", "text": "ok"}]}]}
    proxy_core.normalize_content(body)
    assert body["messages"][0]["content"] == [{"type": "text", "text": "hi"}]
    assert body["messages"][1]["content"] == [{"type": "text", "text": "ok"}]  # unchanged


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


def test_streaming_tool_call_only_starts_at_index_zero():
    """Regression: a response that's *only* a native tool_call (no text delta) must
    still open its first content block at index 0 — Anthropic block indices are
    contiguous from 0, and a stray index 1 first block confuses Claude Code's SSE
    parser (observed as stuck/interrupted tool calls)."""
    import asyncio

    async def fake_lines():
        chunks = [
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "id": "call_1", "function": {"name": "grep_search", "arguments": ""}}]}}]},
            {"choices": [{"delta": {"tool_calls": [
                {"index": 0, "function": {"arguments": "{\"pattern\": \"TODO\"}"}}]}}]},
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        for c in chunks:
            yield "data: " + json.dumps(c)
        yield "data: [DONE]"

    async def run():
        return [e async for e in tx.stream_anthropic_events(fake_lines(), "m")]

    events = asyncio.run(run())
    starts = [json.loads(e.split("data: ", 1)[1]) for e in events if "content_block_start" in e]
    assert len(starts) == 1
    assert starts[0]["index"] == 0
    assert starts[0]["content_block"]["type"] == "tool_use"


def test_handle_openai_mid_stream_timeout_yields_sse_error(monkeypatch):
    """Regression: a stall/timeout *after* the upstream stream has already opened
    (e.g. a reasoning model that goes silent mid-generation) used to propagate an
    uncaught exception out of the async generator, which Starlette turns into a
    connection that just dies with no event — Claude Code sees an empty/interrupted
    turn. It must instead surface as a clean `event: error` SSE frame, matching what
    the "anthropic" flavor's rawstream path already does."""
    import asyncio

    class FakeResp:
        status_code = 200

        async def aiter_lines(self):
            yield "data: " + json.dumps({"choices": [{"delta": {"content": "hi"}}]})
            raise httpx.ReadTimeout("upstream stalled")

        async def aclose(self):
            pass

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def build_request(self, *a, **k):
            return object()

        async def send(self, *a, **k):
            return FakeResp()

        async def aclose(self):
            pass

    monkeypatch.setattr(proxy_core.httpx, "AsyncClient", FakeClient)

    async def run():
        status, gen = await proxy_core.handle_openai(
            ZEN, {"model": ZEN.default_model, "stream": True,
                  "messages": [{"role": "user", "content": "hi"}]})
        assert status == "stream"
        return [e async for e in gen]

    events = asyncio.run(run())
    joined = "".join(events)
    assert "text_delta" in joined      # the pre-timeout chunk still made it through
    assert "event: error" in joined    # and the stall surfaces cleanly, not a dead socket


# ---- retry classification: transient_error_patterns (scoped 400 retry) ----

def test_handle_openai_transient_pattern_retries_chain(monkeypatch):
    """A 400 whose body matches the provider's transient_error_patterns retries the
    fallback chain instead of failing immediately (opencode-go's 'Upstream request
    failed' quirk — confirmed against oc-go-cc's own logs, which recover the same way)."""
    import asyncio

    provider = ProviderConfig(name="go", flavor="openai", base_url="http://u",
                              api_key_env="K", default_fallback=["m2"],
                              transient_error_patterns=["Upstream request failed"])
    calls = []

    class FakeResp:
        def __init__(self, status, text, json_body=None):
            self.status_code = status
            self.text = text
            self._json = json_body

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, headers=None, json=None):
            calls.append(json["model"])
            if json["model"] == "m1":
                return FakeResp(400, '{"error":{"message":"Upstream request failed"}}')
            return FakeResp(200, "", {"choices": [{"message": {"content": "ok"}}], "usage": {}})

        async def aclose(self):
            pass

    monkeypatch.setattr(proxy_core.httpx, "AsyncClient", FakeClient)

    async def run():
        return await proxy_core.handle_openai(
            provider, {"model": "m1", "messages": [{"role": "user", "content": "hi"}]})

    status, result = asyncio.run(run())
    assert calls == ["m1", "m2"]
    assert status == 200


def test_handle_openai_non_matching_400_is_fatal_preserves_message(monkeypatch):
    """A 400 that doesn't match any configured pattern is fatal and immediate — no
    wasted round-trips through the fallback chain, and the real error isn't buried."""
    import asyncio

    provider = ProviderConfig(name="go", flavor="openai", base_url="http://u",
                              api_key_env="K", default_fallback=["m2"],
                              transient_error_patterns=["Upstream request failed"])
    calls = []

    class FakeResp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, headers=None, json=None):
            calls.append(json["model"])
            return FakeResp(400, '{"error":{"message":"model not supported"}}')

        async def aclose(self):
            pass

    monkeypatch.setattr(proxy_core.httpx, "AsyncClient", FakeClient)

    async def run():
        return await proxy_core.handle_openai(
            provider, {"model": "m1", "messages": [{"role": "user", "content": "hi"}]})

    status, result = asyncio.run(run())
    assert calls == ["m1"]
    assert status == 400
    assert "model not supported" in result["error"]["message"]


def test_handle_openai_400_fatal_when_provider_has_no_transient_patterns(monkeypatch):
    """Same error text that would match opencode-go's pattern stays fatal for a
    provider (e.g. zen/nvidia) that hasn't opted into transient_error_patterns —
    the mechanism is scoped per-provider, not global."""
    import asyncio

    assert ZEN.transient_error_patterns == []
    calls = []

    class FakeResp:
        def __init__(self, status, text):
            self.status_code = status
            self.text = text

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, headers=None, json=None):
            calls.append(json["model"])
            return FakeResp(400, "Upstream request failed")

        async def aclose(self):
            pass

    monkeypatch.setattr(proxy_core.httpx, "AsyncClient", FakeClient)

    async def run():
        return await proxy_core.handle_openai(
            ZEN, {"model": ZEN.default_model, "messages": [{"role": "user", "content": "hi"}]})

    status, result = asyncio.run(run())
    assert calls == [ZEN.default_model]
    assert status == 400


def test_handle_anthropic_passthrough_success_and_cache_strip(monkeypatch):
    """Minimal smoke test for the (currently unused-by-any-built-in-provider, but
    live) 'anthropic' passthrough flavor: success path + cache_control stripping."""
    import asyncio

    provider = ProviderConfig(name="anthro", flavor="anthropic", base_url="http://u",
                              api_key_env="K", cache_control_strip=["kimi"])
    seen_bodies = []

    class FakeResp:
        status_code = 200
        text = ""

        def json(self):
            return {"type": "message", "content": [{"type": "text", "text": "hi"}]}

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, headers=None, json=None):
            seen_bodies.append(json)
            return FakeResp()

        async def aclose(self):
            pass

    monkeypatch.setattr(proxy_core.httpx, "AsyncClient", FakeClient)

    async def run():
        return await proxy_core.handle_anthropic(
            provider, {"model": "kimi-k2.7-code", "messages": [{"role": "user", "content": [
                {"type": "text", "text": "hi", "cache_control": {"type": "ephemeral"}}]}]})

    status, result = asyncio.run(run())
    assert status == 200
    assert result["content"][0]["text"] == "hi"
    assert "cache_control" not in seen_bodies[0]["messages"][0]["content"][0]


def test_handle_anthropic_transient_pattern_retries_chain(monkeypatch):
    import asyncio

    provider = ProviderConfig(name="anthro", flavor="anthropic", base_url="http://u",
                              api_key_env="K", default_fallback=["m2"],
                              transient_error_patterns=["Upstream request failed"])
    calls = []

    class FakeResp:
        def __init__(self, status, text, json_body=None):
            self.status_code = status
            self.text = text
            self._json = json_body

        def json(self):
            return self._json

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def post(self, url, headers=None, json=None):
            calls.append(json["model"])
            if json["model"] == "m1":
                return FakeResp(400, '{"error":"Upstream request failed"}')
            return FakeResp(200, "", {"type": "message", "content": []})

        async def aclose(self):
            pass

    monkeypatch.setattr(proxy_core.httpx, "AsyncClient", FakeClient)

    async def run():
        return await proxy_core.handle_anthropic(
            provider, {"model": "m1", "messages": [{"role": "user", "content": "hi"}]})

    status, result = asyncio.run(run())
    assert calls == ["m1", "m2"]
    assert status == 200


# ---- reasoning_content rescue (NIM-style reasoning models) ----

def test_reasoning_only_response_surfaces_reasoning_as_text():
    """A reasoning model that spends the whole budget thinking returns empty content;
    the reasoning must be surfaced instead of an empty turn."""
    data = {"choices": [{"message": {"content": "", "reasoning_content": "thinking hard"},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2}}
    r = tx.openai_to_anthropic_response(data, "m")
    assert r["content"] == [{"type": "text", "text": "thinking hard"}]
    assert r["stop_reason"] == "max_tokens"


def test_reasoning_ignored_when_content_present():
    data = {"choices": [{"message": {"content": "answer", "reasoning_content": "cot"},
                         "finish_reason": "stop"}], "usage": {}}
    r = tx.openai_to_anthropic_response(data, "m")
    assert r["content"] == [{"type": "text", "text": "answer"}]


# ---- error-in-stream (HTTP 200 with an error data chunk, NIM style) ----

def test_handle_openai_error_in_first_stream_chunk_falls_back(monkeypatch):
    """NIM returns HTTP 200 and puts capacity failures INSIDE the stream
    ('ResourceExhausted', code 500). That must classify for fallback like a real
    5xx, not translate into an empty assistant turn."""
    import asyncio

    provider = ProviderConfig(name="nv", flavor="openai", base_url="http://u",
                              api_key_env="K", default_fallback=["m2"])
    calls = []

    def _resp_for(model):
        class FakeResp:
            status_code = 200

            async def aiter_lines(self):
                if model == "m1":
                    yield "data: " + json.dumps({"error": {
                        "message": "ResourceExhausted: Worker local total request limit reached",
                        "code": 500}})
                    yield "data: [DONE]"
                else:
                    yield "data: " + json.dumps({"choices": [{"delta": {"content": "oi"}}]})
                    yield "data: " + json.dumps({"choices": [{"delta": {},
                                                              "finish_reason": "stop"}]})
                    yield "data: [DONE]"

            async def aclose(self):
                pass
        return FakeResp()

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        def build_request(self, method, url, headers=None, json=None):
            calls.append(json["model"])
            return json["model"]

        async def send(self, req, stream=False):
            return _resp_for(req)

        async def aclose(self):
            pass

    monkeypatch.setattr(proxy_core.httpx, "AsyncClient", FakeClient)

    async def run():
        status, gen = await proxy_core.handle_openai(
            provider, {"model": "m1", "stream": True,
                       "messages": [{"role": "user", "content": "hi"}]})
        assert status == "stream"
        return [e async for e in gen]

    events = asyncio.run(run())
    joined = "".join(events)
    assert calls == ["m1", "m2"]           # fell back to the next model
    assert "text_delta" in joined and "oi" in joined
    assert "event: error" not in joined    # the fallback absorbed the failure


def test_stream_mid_stream_error_chunk_yields_error_frame():
    """An error chunk AFTER content already flowed can't fall back (bytes were sent);
    it must surface as a clean `event: error` frame, not a silent empty ending."""
    import asyncio

    async def lines():
        yield "data: " + json.dumps({"choices": [{"delta": {"content": "partial"}}]})
        yield "data: " + json.dumps({"error": {"message": "boom", "code": 500}})
        yield "data: [DONE]"

    async def run():
        return [e async for e in tx.stream_anthropic_events(lines(), "m")]

    joined = "".join(asyncio.run(run()))
    assert "partial" in joined
    assert "event: error" in joined and "boom" in joined
