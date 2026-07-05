"""Anthropic Messages API <-> OpenAI Chat Completions translation.

Extracted and unified from the original claude-zen-proxy / claude-nv-proxy. Parameterized
by ProviderConfig. Fixes carried over from the originals:
- system prompt given as a list of content blocks is flattened (dropping cache_control),
  which the originals only did in the NVIDIA path;
- streaming usage.output_tokens is propagated (the originals hardcoded it to 0).

Tool calls are round-tripped through text markers ([tool_use: name id=.. input=..]) for
backends that don't support native OpenAI tool_calls, in addition to native tool_calls.
"""
from __future__ import annotations

import copy
import json
import logging
import uuid
from typing import AsyncIterator

from .providers import ProviderConfig

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7

log = logging.getLogger("claude_provider_proxy")


# ---------- request: Anthropic -> OpenAI ----------

def _flatten_blocks(content) -> str:
    """A list of Anthropic content blocks -> a single string (OpenAI message content)."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content)
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            parts.append(str(block))
            continue
        t = block.get("type")
        if t == "text":
            parts.append(block.get("text", ""))
        elif t == "image":
            parts.append("[image content]")
        elif t == "tool_use":
            parts.append(f"[tool_use: {block.get('name')} id={block.get('id')} "
                         f"input={json.dumps(block.get('input', {}))}]")
        elif t == "tool_result":
            parts.append(f"[tool_result: {json.dumps(block.get('content', ''))}]")
        elif t == "thinking":
            parts.append(f"[thinking: {block.get('thinking', '')}]")
        elif t == "redacted_thinking":
            parts.append(f"[redacted_thinking: {block.get('data', '')}]")
        elif t == "document":
            src = block.get("source") or {}
            label = block.get("title") or src.get("media_type") or src.get("type") or "unknown"
            parts.append(f"[document: {label}]")
        else:
            parts.append(str(block))
    return "\n".join(parts)


def _flatten_system(system) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return _flatten_blocks(system)
    return str(system or "")


def _non_empty(s: str, placeholder: str) -> str:
    return s if s.strip() else placeholder


def _merge_consecutive(msgs: list[dict]) -> list[dict]:
    """Merge adjacent plain-text messages of the same role. Anthropic allows repeated
    roles; some OpenAI-compatible backends require strict alternation. Never merges
    across tool_calls carriers or role:"tool" messages (each is a distinct result)."""
    merged: list[dict] = []
    for m in msgs:
        prev = merged[-1] if merged else None
        if (prev is not None and prev["role"] == m["role"]
                and m["role"] in ("user", "assistant", "system")
                and "tool_calls" not in prev and "tool_calls" not in m
                and isinstance(prev.get("content"), str) and isinstance(m.get("content"), str)):
            prev["content"] = prev["content"] + "\n" + m["content"]
        else:
            merged.append(dict(m))
    return merged


def convert_messages(messages: list[dict], native_tools: bool = False) -> list[dict]:
    out: list[dict] = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        blocks = content if isinstance(content, list) else []
        tool_results = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_result"]
        tool_uses = [b for b in blocks if isinstance(b, dict) and b.get("type") == "tool_use"]

        if native_tools and role == "user" and tool_results:
            # OpenAI requires role:"tool" replies immediately after the assistant's
            # tool_calls message, so results come before any accompanying user text.
            for b in tool_results:
                res = _flatten_blocks(b.get("content", ""))
                if b.get("is_error"):
                    res = f"[tool error] {res}"
                out.append({"role": "tool",
                            "tool_call_id": b.get("tool_use_id") or "",
                            "content": _non_empty(res, "[empty result]")})
            rest = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "tool_result")]
            if rest:
                out.append({"role": "user",
                            "content": _non_empty(_flatten_blocks(rest), "[empty message]")})
        elif native_tools and role == "assistant" and tool_uses:
            text_blocks = [b for b in blocks if not (isinstance(b, dict) and b.get("type") == "tool_use")]
            out.append({"role": "assistant",
                        "content": _flatten_blocks(text_blocks).strip() or None,
                        "tool_calls": [{"id": b.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                                        "type": "function",
                                        "function": {"name": b.get("name"),
                                                     "arguments": json.dumps(b.get("input", {}))}}
                                       for b in tool_uses]})
        else:
            out.append({"role": role,
                        "content": _non_empty(_flatten_blocks(content), "[empty message]")})
    return _merge_consecutive(out)


def anthropic_to_openai(body: dict, provider: ProviderConfig) -> dict:
    model = body.get("model") or provider.default_model or "gpt-4o-mini"
    max_tokens = body.get("max_tokens", DEFAULT_MAX_TOKENS)
    if model in provider.reasoning_models and max_tokens < provider.min_tokens_reasoning:
        max_tokens = provider.min_tokens_reasoning

    messages_data = body.get("messages", [])
    system = body.get("system")
    if system is None:
        for m in messages_data:
            if m.get("role") == "system":
                system = m.get("content")
                break
    messages_data = [m for m in messages_data if m.get("role") != "system"]

    openai_messages: list[dict] = []
    system_str = _flatten_system(system)
    if system_str:
        openai_messages.append({"role": "system", "content": system_str})
    openai_messages.extend(convert_messages(messages_data,
                                            native_tools=provider.native_tool_history))

    out: dict = {
        "model": model,
        "messages": openai_messages,
        "max_tokens": max_tokens,
        "stream": body.get("stream", False),
    }
    # Only forward temperature if the client set it — some models (e.g. kimi via
    # Moonshot) reject any value other than their default ("only 1 is allowed").
    if "temperature" in body:
        out["temperature"] = body["temperature"]
    # Claude Code sends "tools": [] on tool-less background calls; some backends
    # (e.g. NVIDIA NIM) 400 on an empty tools array — omit the field instead.
    if body.get("tools"):
        out["tools"] = [{"type": "function",
                         "function": {"name": t["name"],
                                      "description": t.get("description", ""),
                                      "parameters": t.get("input_schema", {})}}
                        for t in body["tools"]]
    if "stop_sequences" in body:
        out["stop"] = body["stop_sequences"]
    # tool_choice without tools is invalid on most backends — only forward it
    # alongside a non-empty tools list.
    if "tool_choice" in body and "tools" in out:
        mapped = _map_tool_choice(body["tool_choice"])
        if mapped is not None:
            out["tool_choice"] = mapped
    if "top_p" in body:
        out["top_p"] = body["top_p"]
    # Intentionally not forwarded: top_k, metadata, parallel_tool_calls — no clean
    # OpenAI Chat Completions equivalent.
    thinking = body.get("thinking")
    if (provider.reasoning_extra_body and isinstance(thinking, dict)
            and thinking.get("type") == "enabled" and model in provider.reasoning_models):
        out.update(copy.deepcopy(provider.reasoning_extra_body))
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}
    return out


def _map_tool_choice(tc):
    if not isinstance(tc, dict):
        return None
    t = tc.get("type")
    if t == "auto":
        return "auto"
    if t == "any":
        return "required"
    if t == "tool" and tc.get("name"):
        return {"type": "function", "function": {"name": tc["name"]}}
    return None


# ---------- embedded tool-marker parser (round-trip partner of _flatten_blocks) ----------

TOOL_MARKER_PREFIX = "[tool_use: "


def find_tool_use_blocks(text: str) -> list[dict]:
    blocks, i = [], 0
    while True:
        start = text.find(TOOL_MARKER_PREFIX, i)
        if start < 0:
            break
        j = start + len(TOOL_MARKER_PREFIX)
        # Bound the header search to end before the next marker occurrence (if any), so
        # a malformed header (e.g. the model used "params=" instead of "input=") can't
        # accidentally match a *later*, unrelated marker's " id="/" input=" and misparse
        # across the boundary between the two.
        next_marker = text.find(TOOL_MARKER_PREFIX, j)
        scope_end = next_marker if next_marker >= 0 else len(text)
        name_end = text.find(" id=", j, scope_end)
        id_end = text.find(" input=", name_end, scope_end) if name_end >= 0 else -1
        if name_end < 0 or id_end < 0:
            # Malformed marker header for this occurrence — skip past just it and keep
            # scanning, so a well-formed marker elsewhere in the same text still parses.
            i = j
            continue
        name = text[j:name_end].strip()
        tid = text[name_end + 4:id_end].strip()
        # brace-match the JSON input
        k = text.find("{", id_end, scope_end)
        if k < 0:
            i = j
            continue
        depth, p, in_str, esc = 0, k, False, False
        while p < len(text):
            c = text[p]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        break
            p += 1
        raw = text[k:p + 1]
        try:
            inp = json.loads(raw)
        except Exception:  # noqa: BLE001
            inp = {}
        blocks.append({"start": start, "end": p + 1, "name": name, "id": tid, "input": inp})
        i = p + 1
    return blocks


def split_text_and_tools(text: str):
    found = find_tool_use_blocks(text)
    if not found:
        return None
    out, cursor = [], 0
    for b in found:
        pre = text[cursor:b["start"]].strip()
        if pre:
            out.append({"type": "text", "text": pre})
        out.append({"type": "tool_use", "id": b["id"] or f"toolu_{uuid.uuid4().hex[:24]}",
                    "name": b["name"], "input": b["input"]})
        cursor = b["end"]
    tail = text[cursor:].strip()
    if tail:
        out.append({"type": "text", "text": tail})
    return out


def map_stop_reason(finish: str | None) -> str:
    return {"stop": "end_turn", "length": "max_tokens",
            "tool_calls": "tool_use"}.get(finish or "", "end_turn")


# ---------- response: OpenAI -> Anthropic (non-streaming) ----------

def openai_to_anthropic_response(data: dict, model: str) -> dict:
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message", {}) or {}
    content: list[dict] = []

    text = msg.get("content")
    if text:
        mixed = split_text_and_tools(text)
        if mixed is None and TOOL_MARKER_PREFIX in text:
            log.warning("model=%s emitted a %r marker that failed to parse; "
                        "falling back to raw text: %.200r", model, TOOL_MARKER_PREFIX, text)
        content.extend(mixed if mixed else [{"type": "text", "text": text}])

    for tc in (msg.get("tool_calls") or []):
        fn = tc.get("function", {}) or {}
        try:
            args = json.loads(fn.get("arguments") or "{}")
        except Exception:  # noqa: BLE001
            args = {}
        content.append({"type": "tool_use",
                        "id": tc.get("id") or f"toolu_{uuid.uuid4().hex[:24]}",
                        "name": fn.get("name"), "input": args})

    # Reasoning models (NIM-style) put chain-of-thought in reasoning_content; when the
    # budget runs out mid-reasoning the answer content comes back empty. Surface the
    # reasoning instead of returning an empty turn the client can't act on.
    if not content:
        reasoning = msg.get("reasoning_content") or msg.get("reasoning")
        if reasoning:
            log.warning("model=%s returned reasoning but no content "
                        "(max_tokens too low?); surfacing reasoning as text", model)
            content.append({"type": "text", "text": reasoning})

    usage = data.get("usage", {}) or {}
    return {
        "id": data.get("id") or f"msg_{uuid.uuid4().hex}",
        "type": "message", "role": "assistant", "content": content,
        "model": model,
        "stop_reason": map_stop_reason(choice.get("finish_reason")),
        "stop_sequence": None,
        "usage": {"input_tokens": usage.get("prompt_tokens", 0),
                  "output_tokens": usage.get("completion_tokens", 0)},
    }


# ---------- response: OpenAI SSE -> Anthropic SSE (streaming) ----------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_anthropic_events(lines: AsyncIterator[str], model: str) -> AsyncIterator[str]:
    msg_id = f"msg_{uuid.uuid4().hex}"
    yield _sse("message_start", {"type": "message_start", "message": {
        "id": msg_id, "type": "message", "role": "assistant", "content": [],
        "model": model, "stop_reason": None, "stop_sequence": None,
        "usage": {"input_tokens": 0, "output_tokens": 0}}})

    text_open = False
    full_text: list[str] = []
    reasoning_parts: list[str] = []
    api_tools: dict[int, dict] = {}
    finish_reason = None
    out_tokens = 0

    async for line in lines:
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:].strip()
        if payload == "[DONE]":
            break
        try:
            chunk = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if chunk.get("usage"):
            out_tokens = chunk["usage"].get("completion_tokens", out_tokens)
        choice = (chunk.get("choices") or [{}])[0]
        delta = choice.get("delta", {}) or {}

        # NIM-style reasoning models stream chain-of-thought separately; keep it so a
        # reasoning-only response (budget exhausted mid-thought) isn't an empty turn.
        r = delta.get("reasoning_content") or delta.get("reasoning")
        if r:
            reasoning_parts.append(r)

        if delta.get("content"):
            if not text_open:
                yield _sse("content_block_start", {"type": "content_block_start",
                           "index": 0, "content_block": {"type": "text", "text": ""}})
                text_open = True
            full_text.append(delta["content"])
            yield _sse("content_block_delta", {"type": "content_block_delta", "index": 0,
                       "delta": {"type": "text_delta", "text": delta["content"]}})

        for tc in (delta.get("tool_calls") or []):
            idx = tc.get("index", 0)
            slot = api_tools.setdefault(idx, {"id": None, "name": None, "args": []})
            if tc.get("id"):
                slot["id"] = tc["id"]
            fn = tc.get("function", {}) or {}
            if fn.get("name"):
                slot["name"] = fn["name"]
            if fn.get("arguments"):
                slot["args"].append(fn["arguments"])

        if choice.get("finish_reason"):
            finish_reason = choice["finish_reason"]

    if text_open:
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    # Block indices must be contiguous from 0. If no text block was opened (a
    # tool-calls-only response), tool blocks start at 0, not 1.
    index = 1 if text_open else 0

    def _emit_tool(idx, name, raw_args, inp):
        nonlocal index
        ev = [_sse("content_block_start", {"type": "content_block_start", "index": index,
                   "content_block": {"type": "tool_use",
                                     "id": idx or f"toolu_{uuid.uuid4().hex[:24]}",
                                     "name": name, "input": {}}}),
              _sse("content_block_delta", {"type": "content_block_delta", "index": index,
                   "delta": {"type": "input_json_delta",
                             "partial_json": raw_args if raw_args else json.dumps(inp)}}),
              _sse("content_block_stop", {"type": "content_block_stop", "index": index})]
        index += 1
        return ev

    if api_tools:
        for idx in sorted(api_tools):
            t = api_tools[idx]
            for ev in _emit_tool(t["id"], t["name"], "".join(t["args"]), {}):
                yield ev
    elif not text_open and full_text:
        joined = "".join(full_text)
        yield _sse("content_block_start", {"type": "content_block_start", "index": 0,
                   "content_block": {"type": "text", "text": joined}})
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})
    elif text_open:
        joined = "".join(full_text)
        mixed = split_text_and_tools(joined)
        if mixed is None and TOOL_MARKER_PREFIX in joined:
            log.warning("model=%s emitted a %r marker that failed to parse mid-stream; "
                        "falling back to raw text: %.200r", model, TOOL_MARKER_PREFIX, joined)
        for b in (mixed or []):
            if b["type"] == "tool_use":
                for ev in _emit_tool(b["id"], b["name"], None, b["input"]):
                    yield ev
    elif reasoning_parts:
        # Reasoning-only stream: the model spent the whole budget thinking. Surface the
        # reasoning as text rather than ending the turn with no content at all.
        log.warning("model=%s streamed reasoning but no content "
                    "(max_tokens too low?); surfacing reasoning as text", model)
        yield _sse("content_block_start", {"type": "content_block_start", "index": 0,
                   "content_block": {"type": "text", "text": "".join(reasoning_parts)}})
        yield _sse("content_block_stop", {"type": "content_block_stop", "index": 0})

    yield _sse("message_delta", {"type": "message_delta",
               "delta": {"stop_reason": map_stop_reason(finish_reason or "stop"),
                         "stop_sequence": None},
               "usage": {"output_tokens": out_tokens}})
    yield _sse("message_stop", {"type": "message_stop"})
