# Profiles

A profile maps the four Claude Code model slots to provider model names. Profiles are
**per provider**, stored at `~/.config/claude-provider-proxy/profiles/<provider>/<name>.env`.

```sh
# ~/.config/claude-provider-proxy/profiles/nvidia/fast.env
OPUS_MODEL=deepseek-ai/deepseek-v4-flash
SONNET_MODEL=moonshotai/kimi-k2.6
HAIKU_MODEL=deepseek-ai/deepseek-v4-flash
SUBAGENT_MODEL=deepseek-ai/deepseek-v4-flash
```

These become the `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` and
`CLAUDE_CODE_SUBAGENT_MODEL` env vars Claude Code uses to request a model per slot.

## Resolution order

`--profile <name>` (one-shot) → the provider's `active_profile` file → built-in defaults
(a single sensible default model per provider for any unset slot).

## Commands

```bash
claude-provider-proxy profile <provider> list           # tables profiles + slots, marks active
claude-provider-proxy profile <provider> use <name>     # set active (use 'none' to clear)
claude-provider-proxy profile <provider> show [name]    # print a profile
claude-provider-proxy profile <provider> new <name>     # scaffold a profile
```

## Launch with a profile

```bash
claude-provider-proxy nvidia --profile fast
```

## Fallbacks

Per-slot fallback now lives in the provider's `fallbacks` (see [PROVIDERS.md](PROVIDERS.md)),
which is richer than the old single-subagent fallback. Profiles only set the slots.
