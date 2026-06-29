# Changelog

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
  its Anthropic-compatible endpoint, with cache_control stripping reimplemented in-proxy.
