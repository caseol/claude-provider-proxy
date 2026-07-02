# Profiles

A profile maps the five Claude Code model slots to provider model names. Profiles are
**per provider**, stored at `~/.config/claude-provider-proxy/profiles/<provider>/<name>.env`.

```sh
# ~/.config/claude-provider-proxy/profiles/nvidia/fast.env
FABLE_MODEL=deepseek-ai/deepseek-v4-flash
OPUS_MODEL=deepseek-ai/deepseek-v4-flash
SONNET_MODEL=moonshotai/kimi-k2.6
HAIKU_MODEL=deepseek-ai/deepseek-v4-flash
SUBAGENT_MODEL=deepseek-ai/deepseek-v4-flash
```

These become the `ANTHROPIC_DEFAULT_{FABLE,OPUS,SONNET,HAIKU}_MODEL` and
`CLAUDE_CODE_SUBAGENT_MODEL` env vars Claude Code uses to request a model per slot.

`FABLE_MODEL` is the newest slot (Claude Code ≥ 2.1.198), for Claude Fable 5 — a
Mythos-class tier that sits above Opus. None of the built-in providers (OpenCode Go,
OpenCode Zen, NVIDIA) expose a genuinely stronger model than what's already mapped to
`OPUS_MODEL`, so `claude-proxy profile <provider> new` seeds `FABLE_MODEL` with the same
provider default as the other slots — point it at your provider's actual flagship model if
one exists, otherwise leaving it equal to `OPUS_MODEL` is reasonable. For a genuinely
Anthropic-native provider (`flavor: "anthropic"`), set `FABLE_MODEL=claude-fable-5`.

## Resolution order

`--profile <name>` (one-shot) → the provider's `active_profile` file → built-in defaults
(a single sensible default model per provider for any unset slot).

## Commands

```bash
claude-proxy profile <provider> list           # tables profiles + slots, marks active
claude-proxy profile <provider> use <name>     # set active (use 'none' to clear)
claude-proxy profile <provider> show [name]    # print a profile
claude-proxy profile <provider> new <name>     # scaffold a profile
```

## Launch with a profile

```bash
claude-proxy nvidia --profile fast
```

## Fallbacks

Per-slot fallback now lives in the provider's `fallbacks` (see [PROVIDERS.md](PROVIDERS.md)),
which is richer than the old single-subagent fallback. Profiles only set the slots.
