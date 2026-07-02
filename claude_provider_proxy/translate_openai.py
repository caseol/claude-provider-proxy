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

import json
import uuid
from typing import AsyncIterator

from .providers import ProviderConfig

DEFAULT_MAX_TOKENS = 4096
DEFAULT_TEMPERATURE = 0.7


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
        else:
            parts.append(str(block))
    return "\n".join(parts)


def _flatten_system(system) -> str:
    if isinstance(system, str):
        return system
    if isinstance(system, list):
        return "\n".join(b.get("text", "") for b in system
                         if isinstance(b, dict) and b.get("type") == "text")
    return str(system or "")


def convert_messages(messages: list[dict]) -> list[dict]:
    return [{"role": m.get("role", "user"), "content": _flatten_blocks(m.get("content", ""))}
            for m in messages]


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
    openai_messages.extend(convert_messages(messages_data))

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
    if "tools" in body:
        out["tools"] = [{"type": "function",
                         "function": {"name": t["name"],
                                      "description": t.get("description", ""),
                                      "parameters": t.get("input_schema", {})}}
                        for t in body["tools"]]
    if "stop_sequences" in body:
        out["stop"] = body["stop_sequences"]
    if out["stream"]:
        out["stream_options"] = {"include_usage": True}
    return out


# ---------- embedded tool-marker parser (round-trip partner of _flatten_blocks) ----------

def find_tool_use_blocks(text: str) -> list[dict]:
    blocks, i, marker = [], 0, "[tool_use: "
    while True:
        start = text.find(marker, i)
        if start < 0:
            break
        j = start + len(marker)
        name_end = text.find(" id=", j)
        id_end = text.find(" input=", name_end) if name_end >= 0 else -1
        if name_end < 0 or id_end < 0:
            break
        name = text[j:name_end].strip()
        tid = text[name_end + 4:id_end].strip()
        # brace-match the JSON input
        k = text.find("{", id_end)
        if k < 0:
            break
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
        mixed = split_text_and_tools("".join(full_text))
        for b in (mixed or []):
            if b["type"] == "tool_use":
                for ev in _emit_tool(b["id"], b["name"], None, b["input"]):
                    yield ev

    yield _sse("message_delta", {"type": "message_delta",
               "delta": {"stop_reason": map_stop_reason(finish_reason or "stop"),
                         "stop_sequence": None},
               "usage": {"output_tokens": out_tokens}})
    yield _sse("message_stop", {"type": "message_stop"})
