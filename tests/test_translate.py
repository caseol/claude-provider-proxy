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
    assert o["max_tokens"] == 1024   # bumped for reasoning model


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
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert tx.anthropic_to_openai({**base, "tool_choice": {"type": "auto"}}, ZEN)["tool_choice"] == "auto"
    assert tx.anthropic_to_openai({**base, "tool_choice": {"type": "any"}}, ZEN)["tool_choice"] == "required"
    specific = tx.anthropic_to_openai(
        {**base, "tool_choice": {"type": "tool", "name": "grep"}}, ZEN)["tool_choice"]
    assert specific == {"type": "function", "function": {"name": "grep"}}


def test_tool_choice_absent_or_unrecognized_is_omitted():
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert "tool_choice" not in tx.anthropic_to_openai(base, ZEN)
    weird = {**base, "tool_choice": {"type": "something_new"}}
    assert "tool_choice" not in tx.anthropic_to_openai(weird, ZEN)


def test_top_p_passthrough():
    base = {"model": "m", "messages": [{"role": "user", "content": "x"}]}
    assert "top_p" not in tx.anthropic_to_openai(base, ZEN)
    assert tx.anthropic_to_openai({**base, "top_p": 0.9}, ZEN)["top_p"] == 0.9


def test_top_k_metadata_parallel_tool_calls_not_forwarded():
    body = {"model": "m", "messages": [{"role": "user", "content": "x"}],
            "top_k": 40, "metadata": {"user_id": "abc"}, "parallel_tool_calls": False}
    o = tx.anthropic_to_openai(body, ZEN)
    assert "top_k" not in o and "metadata" not in o and "parallel_tool_calls" not in o


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
