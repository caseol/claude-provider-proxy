"""FastAPI app. Provider chosen by URL path: /{provider}/v1/messages.

Claude Code is pointed at ANTHROPIC_BASE_URL=http://127.0.0.1:<port>/<provider>, so it
issues POST /<provider>/v1/messages — which this app routes to the provider's flavor.
"""
from __future__ import annotations

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import config, proxy_core
from .providers import load_providers

config.load_env()
PROVIDERS = load_providers()

app = FastAPI(title="claude-provider-proxy", version="0.1.0")


@app.get("/healthz")
async def healthz():
    return {"ok": True, "port": config.PORT,
            "providers": {n: {"flavor": p.flavor, "has_key": bool(p.api_key)}
                          for n, p in PROVIDERS.items()}}


@app.get("/{provider}/v1/models")
async def models(provider: str):
    p = PROVIDERS.get(provider)
    if not p:
        return JSONResponse(status_code=404, content={"error": f"unknown provider {provider}"})
    headers = proxy_core._headers(p, anthropic=(p.flavor == "anthropic"))
    try:
        async with httpx.AsyncClient(timeout=30.0) as c:
            r = await c.get(f"{p.base_url}/models", headers=headers)
        return JSONResponse(status_code=r.status_code, content=r.json())
    except Exception as e:  # noqa: BLE001
        return JSONResponse(status_code=502, content={"error": str(e)})


@app.post("/{provider}/v1/messages")
async def messages(provider: str, request: Request):
    p = PROVIDERS.get(provider)
    if not p:
        return JSONResponse(status_code=404,
                            content={"type": "error",
                                     "error": {"type": "not_found_error",
                                               "message": f"unknown provider {provider}"}})
    body = await request.json()
    handler = proxy_core.handle_anthropic if p.flavor == "anthropic" else proxy_core.handle_openai
    status, result = await handler(p, body)

    if status == "stream":          # openai flavor, translated SSE (str chunks)
        return StreamingResponse(result, media_type="text/event-stream")
    if status == "rawstream":       # anthropic passthrough, raw SSE (bytes chunks)
        return StreamingResponse(result, media_type="text/event-stream")
    return JSONResponse(status_code=status, content=result)
