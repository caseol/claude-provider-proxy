# Changelog

## [Unreleased]

### Added
- `FABLE_MODEL` profile slot (`ANTHROPIC_DEFAULT_FABLE_MODEL`), matching Claude Code's new
  Fable model slot (Mythos-class tier, above Opus). `claude-proxy profile <provider> new`
  now seeds all five slots; `list`/`show` display `FABLE_MODEL` alongside the others.

## [0.1.0] — Initial release

Unifies three separate Claude Code backend proxies (OpenCode Go, OpenCode Zen, NVIDIA NIM)
into one configurable daemon.

### Added
- Single FastAPI daemon (port 3460); provider selected by URL path
  (`/{provider}/v1/messages`).
- Two flavors: `openai` (Anthropic↔OpenAI translation) and `anthropic` (passthrough +
  per-model `cache_control` stripping).
- Config-driven providers (`providers.json`) with built-ins for opencode-go / opencode-zen
  / nvidia; keys from a local `.env`.
- Per-provider profiles (`OPUS/SONNET/HAIKU/SUBAGENT`, `active_profile`, `--profile`,
  `profile list|use|show|new`).
- Generalized ordered fallback chains (per-model + default).
- `bin/claude-provider-proxy` launcher with daemon control and A2A `AGENT_LANE` export.
- Offline unit tests for the translation core; docs; CI.

### Fixed (vs the original per-provider proxies)
- system prompt given as a content-block list is flattened in all flavors (was NVIDIA-only).
- streaming `usage.output_tokens` is propagated (was hardcoded to 0).

### Dropped
- The `oc-go-cc` Go binary and `cache-strip-proxy` — OpenCode Go is now called directly via
  its OpenAI-compatible endpoint (`openai` flavor), reusing the shared translation core. Its
  native Anthropic endpoint is avoided because it mistranslates tools to Moonshot/kimi
  ("function name is invalid"). Temperature is only forwarded when the client sets it (kimi
  rejects non-default values).
