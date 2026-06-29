# claude-provider-proxy

[![tests](https://github.com/caseol/claude-provider-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/caseol/claude-provider-proxy/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**One local proxy that runs [Claude Code](https://claude.com/claude-code) against any model backend — OpenCode Go, OpenCode Zen, NVIDIA NIM, or your own OpenAI/Anthropic-compatible endpoint — with per-provider profiles. Pick the provider; keep the Claude Code experience.**

Claude Code speaks the Anthropic Messages API. This proxy exposes that API locally, routes each request to the **provider you choose** (by URL path), translates Anthropic↔OpenAI when needed, and streams the response back. A single daemon serves all providers at once.

> Replaces three separate proxies (one per backend) with **one configurable daemon**. Local, `127.0.0.1`-only.

## Why

Running Claude Code on third-party/cheaper/free model backends meant a separate bespoke proxy per provider (different ports, near-duplicated translation code, per-provider launchers). This unifies them: **one daemon, provider selected at launch, profiles per provider**, and an easy way to add new providers via config.

## Features

- **Multi-provider, one daemon** — provider chosen by URL path (`/opencode-zen`, `/opencode-go`, `/nvidia`, …). Run several at once.
- **Two translation flavors**:
  - `openai` — full Anthropic↔OpenAI Chat Completions translation (system prompts, content blocks, tools, streaming SSE, tool-call round-tripping). For OpenCode Zen, NVIDIA, and any OpenAI-compatible endpoint.
  - `anthropic` — passthrough to a native Anthropic endpoint (for OpenCode Go), with per-model `cache_control` stripping (kimi rejects it).
- **Per-provider profiles** — `OPUS/SONNET/HAIKU/SUBAGENT` model slots, `active_profile`, `--profile`, and `profile list|use|show|new` — same workflow as before.
- **Ordered fallback chains** — per-model fallback on overload/quota/5xx (generalizes the old single-subagent fallback).
- **Config-driven** — add/override providers in `providers.json`; keys in a local `.env`.
- **Streaming** — Anthropic SSE out, with `usage.output_tokens` correctly propagated.
- **A2A-ready** — sets `AGENT_LANE` so it plugs into the [agent-to-agent](docs/A2A-INTEGRATION.md) setup.

## How it works

```
Claude Code ──► ANTHROPIC_BASE_URL=http://127.0.0.1:3460/<provider>
            ──► POST /<provider>/v1/messages
                 ├─ flavor "openai":   Anthropic→OpenAI → {base}/chat/completions → back to Anthropic
                 └─ flavor "anthropic": passthrough (+cache_control strip) → {base}/messages
```

One FastAPI daemon (port 3460). The `bin/claude-proxy` launcher resolves the
profile, exports the model-slot env, points Claude Code at the right provider path, and
execs `claude`.

## Requirements

- Claude Code on `PATH` as `claude`.
- Python 3.11+ with `fastapi`, `uvicorn`, `httpx` (`requirements.txt`).
- API key(s) for the provider(s) you use.

## Install

```bash
git clone https://github.com/caseol/claude-provider-proxy
cd claude-provider-proxy
python3 -m pip install -r requirements.txt
./install.sh          # deploys the launcher, seeds config, imports existing profiles/keys if present
```

Put your keys in `~/.config/claude-provider-proxy/.env`:

```
ZEN_API_KEY=sk-...
NVIDIA_API_KEY=nvapi-...
OC_GO_CC_API_KEY=sk-opencode-...
```

## Usage

```bash
# launch Claude Code on a provider (starts the daemon if needed)
claude-proxy opencode-zen
claude-proxy nvidia --profile default
claude-proxy opencode-go -- --continue        # args after -- go to claude

# profiles (per provider)
claude-proxy profile opencode-zen list
claude-proxy profile opencode-zen use default
claude-proxy profile nvidia new fast

# daemon control
claude-proxy daemon start|stop|status|logs
```

Call the API directly (any HTTP client):

```bash
curl -s -XPOST 127.0.0.1:3460/opencode-zen/v1/messages -H 'content-type: application/json' \
  -d '{"model":"deepseek-v4-flash-free","max_tokens":50,
       "messages":[{"role":"user","content":"hi"}]}'
```

See [`examples/curl_examples.sh`](examples/curl_examples.sh).

## Configuration

- **Providers** — built-in: `opencode-go`, `opencode-zen`, `nvidia`. Override or add your own in `~/.config/claude-provider-proxy/providers.json` (see [`config/providers.example.json`](config/providers.example.json) and [docs/PROVIDERS.md](docs/PROVIDERS.md)).
- **Profiles** — `~/.config/claude-provider-proxy/profiles/<provider>/<name>.env` with the four model slots. [docs/PROFILES.md](docs/PROFILES.md).
- **Keys** — `~/.config/claude-provider-proxy/.env`.
- **Port** — `CLAUDE_PROVIDER_PROXY_PORT` (default 3460).

## Docs

- [PROVIDERS.md](docs/PROVIDERS.md) — provider config, flavors, fallbacks, adding a provider
- [PROFILES.md](docs/PROFILES.md) — model slots & profile management
- [API.md](docs/API.md) — HTTP endpoints
- [TRANSLATION.md](docs/TRANSLATION.md) — how Anthropic↔OpenAI mapping works & its limits
- [A2A-INTEGRATION.md](docs/A2A-INTEGRATION.md) — agent-to-agent lanes
- [SECURITY.md](docs/SECURITY.md) · [TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md)

## Nuances & limits

- **Tool calls** are round-tripped as text markers (`[tool_use: …]`) in addition to native OpenAI `tool_calls`, so backends without native tool support still work — but fidelity depends on the model emitting the marker format.
- **Images** in the OpenAI flavor are dropped (replaced with `[image content]`).
- **Fallback** triggers on connection errors / 429 / 5xx — not on hard `400`s (e.g. "model not supported"), which return as-is.
- **OpenCode Go** is called directly (no `oc-go-cc` binary); the binary's scenario-routing (auto long-context switch, per-scenario temps) is **not** reproduced — Claude Code pins models per slot anyway.
- Single-machine, `127.0.0.1` only. Keys live in one local `.env`.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with Anthropic, OpenCode, or NVIDIA.
