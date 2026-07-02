"""Upstream orchestration: per-flavor request handling with ordered fallback chains.

- flavor "openai":    translate Anthropic->OpenAI, POST {base}/chat/completions,
                      translate the response (or SSE) back to Anthropic.
- flavor "anthropic": passthrough to {base}/messages (Claude Code already speaks
                      Anthropic), stripping cache_control for strict models (e.g. kimi).
"""
from __future__ import annotations

import copy
import json
from typing import AsyncIterator

import httpx

from . import translate_openai as tx
from .providers import RETRYABLE_STATUS, ProviderConfig

CHAT_TIMEOUT = httpx.Timeout(300.0, connect=15.0)


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
                if resp.status_code in RETRYABLE_STATUS:
                    txt = (await resp.aread()).decode("utf-8", "replace")
                    await resp.aclose(); await client.aclose()
                    last_err = _err(resp.status_code, txt[:300]); continue
                if resp.status_code != 200:
                    txt = (await resp.aread()).decode("utf-8", "replace")
                    await resp.aclose(); await client.aclose()
                    return resp.status_code, _err(resp.status_code, txt[:300])

                async def gen():
                    try:
                        async for ev in tx.stream_anthropic_events(resp.aiter_lines(), model):
                            yield ev
                    except (httpx.RemoteProtocolError, httpx.StreamError, httpx.HTTPError) as e:
                        yield (f"event: error\ndata: "
                               f"{json.dumps(_err(502, str(e)))}\n\n")
                    finally:
                        await resp.aclose(); await client.aclose()
                return "stream", gen()
            else:
                resp = await client.post(url, headers=headers, json=oai)
                await client.aclose()
                if resp.status_code in RETRYABLE_STATUS:
                    last_err = _err(resp.status_code, resp.text[:300]); continue
                if resp.status_code != 200:
                    return resp.status_code, _err(resp.status_code, resp.text[:300])
                return 200, tx.openai_to_anthropic_response(resp.json(), model)
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as e:
            await client.aclose()
            last_err = _err(502, f"upstream error: {e}"); continue
        except Exception as e:  # noqa: BLE001
            await client.aclose()
            return 502, _err(502, str(e))
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
                if resp.status_code in RETRYABLE_STATUS:
                    txt = (await resp.aread()).decode("utf-8", "replace")
                    await resp.aclose(); await client.aclose()
                    last_err = _err(resp.status_code, txt[:300]); continue
                if resp.status_code != 200:
                    txt = (await resp.aread()).decode("utf-8", "replace")
                    await resp.aclose(); await client.aclose()
                    return resp.status_code, _err(resp.status_code, txt[:300])

                async def gen():
                    try:
                        async for chunk in resp.aiter_raw():
                            yield chunk
                    except (httpx.RemoteProtocolError, httpx.StreamError, httpx.HTTPError) as e:
                        yield (f"event: error\ndata: "
                               f"{json.dumps(_err(502, str(e)))}\n\n").encode()
                    finally:
                        await resp.aclose(); await client.aclose()
                return "rawstream", gen()
            else:
                resp = await client.post(url, headers=headers, json=out)
                await client.aclose()
                if resp.status_code in RETRYABLE_STATUS:
                    last_err = _err(resp.status_code, resp.text[:300]); continue
                # pass the upstream Anthropic response (success or hard error) through
                try:
                    return resp.status_code, resp.json()
                except Exception:  # noqa: BLE001
                    return resp.status_code, _err(resp.status_code, resp.text[:300])
        except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException) as e:
            await client.aclose()
            last_err = _err(502, f"upstream error: {e}"); continue
        except Exception as e:  # noqa: BLE001
            await client.aclose()
            return 502, _err(502, str(e))
    return 502, last_err
