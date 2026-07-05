# claude-provider-proxy

[![tests](https://github.com/caseol/claude-provider-proxy/actions/workflows/tests.yml/badge.svg)](https://github.com/caseol/claude-provider-proxy/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)

**One local proxy that runs [Claude Code](https://claude.com/claude-code) against any model backend — OpenCode Go, OpenCode Zen, NVIDIA NIM, OpenRouter, or your own OpenAI/Anthropic-compatible endpoint — with per-provider profiles. Pick the provider; keep the Claude Code experience.**

Claude Code speaks the Anthropic Messages API. This proxy exposes that API locally, routes each request to the **provider you choose** (by URL path), translates Anthropic↔OpenAI when needed, and streams the response back. A single daemon serves all providers at once.

> Replaces three separate proxies (one per backend) with **one configurable daemon**. Local, `127.0.0.1`-only.

## Why

Running Claude Code on third-party/cheaper/free model backends meant a separate bespoke proxy per provider (different ports, near-duplicated translation code, per-provider launchers). This unifies them: **one daemon, provider selected at launch, profiles per provider**, and an easy way to add new providers via config.

## Features

- **Multi-provider, one daemon** — provider chosen by URL path (`/opencode-zen`, `/opencode-go`, `/nvidia`, `/openrouter`, …). Run several at once.
- **Two translation flavors**:
  - `openai` — full Anthropic↔OpenAI Chat Completions translation (system prompts, content blocks, tools, streaming SSE, tool-call round-tripping). Used by **OpenCode Go, OpenCode Zen, NVIDIA, OpenRouter**, and any OpenAI-compatible endpoint.
  - `anthropic` — passthrough to a genuinely Anthropic-native endpoint, with per-model `cache_control` stripping. (OpenCode Go is served via the `openai` flavor — its native Anthropic endpoint mistranslates tools.)
- **Per-provider profiles** — `FABLE/OPUS/SONNET/HAIKU/SUBAGENT` model slots, `active_profile`, `--profile`, and `profile list|use|show|new` — same workflow as before.
- **Ordered fallback chains** — per-model fallback on overload/quota/5xx (generalizes the old single-subagent fallback).
- **Config-driven** — add/override providers in `providers.json`; keys in a local `.env`. Per-provider knobs for reasoning injection (`reasoning_extra_body`), extra HTTP headers (`extra_headers`, e.g. OpenRouter `HTTP-Referer`/`X-Title` attribution), reasoning-token floors, and native tool-history replay.
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

### Provider keys

Keys live in `~/.config/claude-provider-proxy/.env` (one `KEY=value` per line; real
environment variables win over the file). Each built-in provider reads a specific env var
(the `api_key_env` field). You only need keys for the providers you actually launch.

| Provider | `.env` variable | Default model | Where to get the key |
|---|---|---|---|
| `opencode-go` | `OC_GO_CC_API_KEY` | `kimi-k2.7-code` | [opencode.ai](https://opencode.ai) Zen dashboard |
| `opencode-zen` | `ZEN_API_KEY` | `deepseek-v4-flash-free` | [opencode.ai](https://opencode.ai) Zen dashboard |
| `nvidia` | `NVIDIA_API_KEY` | `deepseek-ai/deepseek-v4-flash` | [build.nvidia.com](https://build.nvidia.com) (NIM) |
| `openrouter` | `OPENROUTER_API_KEY` | `deepseek/deepseek-v4-flash` | [openrouter.ai/keys](https://openrouter.ai/keys) |

```
# ~/.config/claude-provider-proxy/.env
OC_GO_CC_API_KEY=sk-opencode-...
ZEN_API_KEY=sk-...
NVIDIA_API_KEY=nvapi-...
OPENROUTER_API_KEY=sk-or-...
```

A provider added in `providers.json` names its own `api_key_env`; add that variable to the
same `.env`. Check which providers the daemon sees a key for:

```bash
curl -s 127.0.0.1:3460/healthz    # -> {"providers":{"openrouter":{"has_key":true},…}}
```

## Usage

```bash
# launch Claude Code on a provider (starts the daemon if needed)
claude-proxy openrouter
claude-proxy nvidia --profile fast              # one-shot profile override
claude-proxy opencode-go -- --continue          # args after -- go to claude

# daemon control (shared by all providers)
claude-proxy daemon start|stop|status|logs
```

The launcher resolves the profile, seeds the five model slots, points Claude Code at
`http://127.0.0.1:3460/<provider>`, and execs `claude`.

### Profile manager & model mapping

A **profile** maps Claude Code's five model slots to concrete provider model names, and is
stored **per provider** at `~/.config/claude-provider-proxy/profiles/<provider>/<name>.env`.
The slots become the env vars Claude Code reads to request a model per tier:

| Slot (`.env`) | Claude Code env var | Tier |
|---|---|---|
| `FABLE_MODEL` | `ANTHROPIC_DEFAULT_FABLE_MODEL` | Fable 5 (newest, above Opus) |
| `OPUS_MODEL` | `ANTHROPIC_DEFAULT_OPUS_MODEL` | Opus |
| `SONNET_MODEL` | `ANTHROPIC_DEFAULT_SONNET_MODEL` | Sonnet (main workhorse) |
| `HAIKU_MODEL` | `ANTHROPIC_DEFAULT_HAIKU_MODEL` | Haiku (fast/background) |
| `SUBAGENT_MODEL` | `CLAUDE_CODE_SUBAGENT_MODEL` | Subagents |

No profile is required to start — `claude-proxy <provider>` maps every slot to the
provider default (e.g. `deepseek/deepseek-v4-flash` for `openrouter`). Create a profile
only when you want per-tier control.

Profiles are managed with the **`profile`** subcommand (provider comes *after* it):

```bash
claude-proxy profile openrouter list            # tables profiles + slots, marks active
claude-proxy profile openrouter new fast        # scaffold (5 slots seeded with the provider default)
$EDITOR ~/.config/claude-provider-proxy/profiles/openrouter/fast.env
claude-proxy profile openrouter show [name]     # print a profile (active if omitted)
claude-proxy profile openrouter use fast        # set active ('none'/'clear' to reset to defaults)
```

> **Mind the syntax.** `claude-proxy profile openrouter list` **manages** profiles, whereas
> `claude-proxy openrouter --profile <name>` **launches** Claude Code with an existing
> profile. So `claude-proxy openrouter --profile list` looks for a profile literally named
> `list` (and fails) — it does not list anything.

Example `profiles/openrouter/fast.env` — cheap workhorse, a stronger model reserved for Opus:

```sh
FABLE_MODEL=deepseek/deepseek-v4-pro
OPUS_MODEL=deepseek/deepseek-v4-pro
SONNET_MODEL=deepseek/deepseek-v4-flash
HAIKU_MODEL=deepseek/deepseek-v4-flash
SUBAGENT_MODEL=deepseek/deepseek-v4-flash
```

**Model-slot resolution order:** `--profile <name>` (one-shot) → the provider's
`active_profile` file → the built-in provider default for any unset slot. Model names are
whatever the backend expects (OpenRouter/NVIDIA slugs are namespaced, e.g.
`deepseek/deepseek-v4-flash`). See [docs/PROFILES.md](docs/PROFILES.md).

Ready-made per-family profiles for OpenRouter (Claude, DeepSeek, Qwen, Kimi) ship in
[`examples/profiles/openrouter/`](examples/profiles/openrouter/) — copy one into
`~/.config/claude-provider-proxy/profiles/openrouter/<name>.env` to get started.

### Call the API directly

Any HTTP client can hit the Anthropic Messages endpoint per provider:

```bash
curl -s -XPOST 127.0.0.1:3460/openrouter/v1/messages -H 'content-type: application/json' \
  -d '{"model":"deepseek/deepseek-v4-flash","max_tokens":50,
       "messages":[{"role":"user","content":"hi"}]}'

curl -s 127.0.0.1:3460/openrouter/v1/models       # list the provider's catalog
```

See [`examples/curl_examples.sh`](examples/curl_examples.sh).

## Configuration

- **Providers** — built-in: `opencode-go`, `opencode-zen`, `nvidia`, `openrouter`. Override or add your own in `~/.config/claude-provider-proxy/providers.json` (see [`config/providers.example.json`](config/providers.example.json) and [docs/PROVIDERS.md](docs/PROVIDERS.md)).
- **Profiles** — `~/.config/claude-provider-proxy/profiles/<provider>/<name>.env` with the five model slots. [docs/PROFILES.md](docs/PROFILES.md).
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

- **Tool calls** are round-tripped as text markers (`[tool_use: …]`) in addition to native OpenAI `tool_calls`, so backends without native tool support still work — but fidelity depends on the model emitting the marker format. Providers verified to support it can opt into native tool-history replay (`assistant.tool_calls` + `role:"tool"`) via `native_tool_history`; markers remain the default fallback.
- **Images** in the OpenAI flavor are dropped (replaced with `[image content]`).
- **Fallback** triggers on connection errors / 429 / 5xx, and on `400`s only when a provider
  explicitly marks the error body as transient via `transient_error_patterns` (e.g. OpenCode
  Go's generic "Upstream request failed"). Other hard `400`s (e.g. "model not supported")
  return as-is.
- **OpenCode Go** is called directly (no `oc-go-cc` binary); the binary's scenario-routing (auto long-context switch, per-scenario temps) is **not** reproduced — Claude Code pins models per slot anyway.
- Single-machine, `127.0.0.1` only. Keys live in one local `.env`.

## License

MIT — see [LICENSE](LICENSE). Not affiliated with Anthropic, OpenCode, or NVIDIA.
