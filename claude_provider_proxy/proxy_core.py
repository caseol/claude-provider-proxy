"""Upstream orchestration: per-flavor request handling with ordered fallback chains.

- flavor "openai":    translate Anthropic->OpenAI, POST {base}/chat/completions,
                      translate the response (or SSE) back to Anthropic.
- flavor "anthropic": passthrough to {base}/messages (Claude Code already speaks
                      Anthropic), stripping cache_control for strict models (e.g. kimi).
"""
from __future__ import annotations

import copy
import json
import logging
import time
from typing import AsyncIterator

import httpx

from . import translate_openai as tx
from .providers import CONFIG_DIR, RETRYABLE_STATUS, ProviderConfig

CHAT_TIMEOUT = httpx.Timeout(300.0, connect=15.0)
ERROR_DUMP_DIR = CONFIG_DIR / "error-dumps"
log = logging.getLogger("claude_provider_proxy")


def _log_fatal(provider: ProviderConfig, model: str, status: int, body_text: str,
               request_body: dict) -> None:
    """Log a non-retryable upstream error WITH its body, and dump the exact request
    that triggered it for post-mortem. One dump file per provider+status (overwritten),
    so disk use stays bounded."""
    snippet = (body_text or "")[:300].replace("\n", " ")
    log.warning("%s: %s -> %s non-retryable: %s", provider.name, model, status, snippet)
    try:
        ERROR_DUMP_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{provider.name}-{status}.json"
        (ERROR_DUMP_DIR / fname).write_text(json.dumps({
            "when": time.strftime("%Y-%m-%d %H:%M:%S"),
            "provider": provider.name,
            "model": model,
            "status": status,
            "error_body": (body_text or "")[:4000],
            "request": request_body,
        }, ensure_ascii=False, indent=2))
        log.warning("request dumped to %s", ERROR_DUMP_DIR / fname)
    except Exception as e:  # noqa: BLE001 — diagnostics must never break serving
        log.warning("error-dump failed: %s", e)


def strip_cache_control(obj):
    """Recursively remove every cache_control field (in place) and return obj."""
    if isinstance(obj, dict):
        obj.pop("cache_control", None)
        for v in obj.values():
            strip_cache_control(v)
    elif isinstance(obj, list):
        for item in obj:
            strip_cache_control(item)
    return obj


def _headers(provider: ProviderConfig, anthropic: bool) -> dict:
    h = {"content-type": "application/json"}
    key = provider.api_key
    if provider.auth == "x-api-key":
        h["x-api-key"] = key
    else:
        h["authorization"] = f"Bearer {key}"
    if anthropic:
        h["anthropic-version"] = "2023-06-01"
    if provider.user_agent:
        h["user-agent"] = provider.user_agent
    return h


def _err(status: int, message: str) -> dict:
    return {"type": "error", "error": {"type": "api_error", "message": message}}


def normalize_content(body: dict) -> dict:
    """Some Anthropic-compatible upstreams (e.g. OpenCode Go) reject a plain-string
    message content and require an array of blocks. Claude Code already sends blocks;
    this makes the passthrough tolerant of string content too."""
    for m in body.get("messages", []):
        if isinstance(m.get("content"), str):
            m["content"] = [{"type": "text", "text": m["content"]}]
    return body


# ---------- flavor: openai (translate) ----------

async def handle_openai(provider: ProviderConfig, body: dict):
    """Returns (status, anthropic_json) for non-stream, or ('stream', async-gen) for stream."""
    requested = body.get("model") or provider.default_model
    stream = bool(body.get("stream"))
    url = f"{provider.base_url}/chat/completions"
    headers = _headers(provider, anthropic=False)
    last_err = _err(502, "all models failed")

    for model in provider.chain_for(requested):
        oai = tx.anthropic_to_openai({**body, "model": model}, provider)
        client = httpx.AsyncClient(timeout=CHAT_TIMEOUT)
        try:
            if stream:
                # open the stream; only fall back on pre-stream failures
                req = client.build_request("POST", url, headers=headers, json=oai)
                resp = await client.send(req, stream=True)
                if resp.status_code != 200:
                    txt = (await resp.aread()).decode("utf-8", "replace")
                    await resp.aclose(); await client.aclose()
                    if resp.status_code in RETRYABLE_STATUS or provider.matches_transient_pattern(txt):
                        log.warning("%s: %s -> %s (pre-stream), falling back",
                                    provider.name, model, resp.status_code)
                        last_err = _err(resp.status_code, txt[:300]); continue
                    _log_fatal(provider, model, resp.status_code, txt, oai)
                    return resp.status_code, _err(resp.status_code, txt[:300])

                # Some upstreams (NVIDIA NIM) return HTTP 200 and deliver the failure
                # INSIDE the stream as the first data chunk ({"error": {...}}), e.g.
                # "ResourceExhausted: Worker local total request limit reached". Peek
                # the first chunk so those still classify for fallback instead of
                # translating into an empty assistant turn.
                line_iter = resp.aiter_lines()
                buffered: list[str] = []
                stream_err = None
                async for line in line_iter:
                    buffered.append(line)
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:].strip()
                    if payload == "[DONE]":
                        break
                    try:
                        first = json.loads(payload)
                    except json.JSONDecodeError:
                        break
                    if isinstance(first.get("error"), dict):
                        stream_err = first["error"]
                    break
                if stream_err is not None:
                    code = stream_err.get("code")
                    status = code if isinstance(code, int) and 400 <= code < 600 else 502
                    txt = json.dumps(stream_err)
                    await resp.aclose(); await client.aclose()
                    if status in RETRYABLE_STATUS or provider.matches_transient_pattern(txt):
                        log.warning("%s: %s -> %s (error-in-stream), falling back",
                                    provider.name, model, status)
                        last_err = _err(status, txt[:300]); continue
                    _log_fatal(provider, model, status, txt, oai)
                    return status, _err(status, txt[:300])

                async def _replay(buf=buffered, rest=line_iter):
                    for bl in buf:
                        yield bl
                    async for rl in rest:
                        yield rl

                async def gen(lines=_replay()):
                    try:
                        async for ev in tx.stream_anthropic_events(lines, model):
                            yield ev
                    except (httpx.RemoteProtocolError, httpx.StreamError, httpx.HTTPError) as e:
                        log.warning("%s: %s stalled mid-stream: %s", provider.name, model, e)
                        yield (f"event: error\ndata: "
                               f"{json.dumps(_err(502, str(e)))}\n\n")
                    finally:
                        await resp.aclose(); await client.aclose()
                return "stream", gen()
            else:
                resp = await client.post(url, headers=headers, json=oai)
                await client.aclose()
                if resp.status_code != 200:
                    if resp.status_code in RETRYABLE_STATUS or provider.matches_transient_pattern(resp.text):
                        log.warning("%s: %s -> %s, falling back",
                                    provider.name, model, resp.status_code)
                        last_err = _err(resp.status_code, resp.text[:300]); continue
                    _log_fatal(provider, model, resp.status_code, resp.text, oai)
                    return resp.status_code, _err(resp.status_code, resp.text[:300])
                return 200, tx.openai_to_anthropic_response(resp.json(), model)
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as e:
            await client.aclose()
            log.warning("%s: %s raised %s, falling back", provider.name, model, e)
            last_err = _err(502, f"upstream error: {e}"); continue
        except Exception as e:  # noqa: BLE001
            await client.aclose()
            log.warning("%s: %s raised unexpected %s", provider.name, model, e)
            return 502, _err(502, str(e))
    log.warning("%s: fallback chain exhausted for %s", provider.name, requested)
    return 502, last_err


# ---------- flavor: anthropic (passthrough) ----------

async def handle_anthropic(provider: ProviderConfig, body: dict):
    requested = body.get("model") or provider.default_model
    stream = bool(body.get("stream"))
    url = f"{provider.base_url}/messages"
    last_err = _err(502, "all models failed")

    for model in provider.chain_for(requested):
        out = copy.deepcopy(body)
        out["model"] = model
        normalize_content(out)
        if provider.strip_cache_control_for(model):
            strip_cache_control(out)
        headers = _headers(provider, anthropic=True)
        client = httpx.AsyncClient(timeout=CHAT_TIMEOUT)
        try:
            if stream:
                req = client.build_request("POST", url, headers=headers, json=out)
                resp = await client.send(req, stream=True)
                if resp.status_code != 200:
                    txt = (await resp.aread()).decode("utf-8", "replace")
                    await resp.aclose(); await client.aclose()
                    if resp.status_code in RETRYABLE_STATUS or provider.matches_transient_pattern(txt):
                        log.warning("%s: %s -> %s (pre-stream), falling back",
                                    provider.name, model, resp.status_code)
                        last_err = _err(resp.status_code, txt[:300]); continue
                    _log_fatal(provider, model, resp.status_code, txt, out)
                    return resp.status_code, _err(resp.status_code, txt[:300])

                async def gen():
                    try:
                        async for chunk in resp.aiter_raw():
                            yield chunk
                    except (httpx.RemoteProtocolError, httpx.StreamError, httpx.HTTPError) as e:
                        log.warning("%s: %s stalled mid-stream: %s", provider.name, model, e)
                        yield (f"event: error\ndata: "
                               f"{json.dumps(_err(502, str(e)))}\n\n").encode()
                    finally:
                        await resp.aclose(); await client.aclose()
                return "rawstream", gen()
            else:
                resp = await client.post(url, headers=headers, json=out)
                await client.aclose()
                if resp.status_code != 200 and (resp.status_code in RETRYABLE_STATUS
                                                 or provider.matches_transient_pattern(resp.text)):
                    log.warning("%s: %s -> %s, falling back",
                                provider.name, model, resp.status_code)
                    last_err = _err(resp.status_code, resp.text[:300]); continue
                # pass the upstream Anthropic response (success or hard error) through
                if resp.status_code != 200:
                    _log_fatal(provider, model, resp.status_code, resp.text, out)
                try:
                    return resp.status_code, resp.json()
                except Exception:  # noqa: BLE001
                    return resp.status_code, _err(resp.status_code, resp.text[:300])
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as e:
            await client.aclose()
            log.warning("%s: %s raised %s, falling back", provider.name, model, e)
            last_err = _err(502, f"upstream error: {e}"); continue
        except Exception as e:  # noqa: BLE001
            await client.aclose()
            log.warning("%s: %s raised unexpected %s", provider.name, model, e)
            return 502, _err(502, str(e))
    log.warning("%s: fallback chain exhausted for %s", provider.name, requested)
    return 502, last_err
