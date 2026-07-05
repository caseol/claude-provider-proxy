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

## Example profiles

Ready-made per-family profiles for `openrouter` (copy into
`~/.config/claude-provider-proxy/profiles/openrouter/<name>.env`, dropping the `.example`
suffix):

| File | Family | Flagship (Opus/Fable) | Workhorse (Sonnet/Subagent) | Fast (Haiku) |
|---|---|---|---|---|
| [`classic.env.example`](../examples/profiles/openrouter/classic.env.example) | Claude (native, via OpenRouter) | `claude-opus-4.8` | `claude-sonnet-5` | `claude-haiku-4.5` |
| [`deepseek.env.example`](../examples/profiles/openrouter/deepseek.env.example) | DeepSeek v4 | `deepseek-v4-pro` | `deepseek-v4-flash` | `deepseek-v4-flash` |
| [`qwen.env.example`](../examples/profiles/openrouter/qwen.env.example) | Qwen3 | `qwen3.7-max` | `qwen3.7-plus` | `qwen3.6-flash` |
| [`kimi.env.example`](../examples/profiles/openrouter/kimi.env.example) | Kimi (Moonshot AI) | `kimi-k2.7-code` | `kimi-k2.5` | `kimi-k2` |

```bash
mkdir -p ~/.config/claude-provider-proxy/profiles/openrouter
cp examples/profiles/openrouter/deepseek.env.example \
   ~/.config/claude-provider-proxy/profiles/openrouter/deepseek.env
claude-proxy profile openrouter use deepseek
```

## Fallbacks

Per-slot fallback now lives in the provider's `fallbacks` (see [PROVIDERS.md](PROVIDERS.md)),
which is richer than the old single-subagent fallback. Profiles only set the slots.
