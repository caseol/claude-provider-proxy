# Changelog

## [Unreleased]

### Added
- `FABLE_MODEL` profile slot (`ANTHROPIC_DEFAULT_FABLE_MODEL`), matching Claude Code's new
  Fable model slot (Mythos-class tier, above Opus). `claude-proxy profile <provider> new`
  now seeds all five slots; `list`/`show` display `FABLE_MODEL` alongside the others.

### Fixed
- `load_providers()` crashed the daemon (`KeyError: 'base_url'`) if `providers.json`
  contained a top-level `"_comment"` key — exactly what `config/providers.example.json`
  ships and what the docs tell you to copy. The merge loop now skips `_`-prefixed and
  non-dict top-level entries instead of feeding them to `_make()`.
- (`openai` flavor, streaming) A tool-calls-only response (no text) opened its first
  `content_block_start` at index 1 instead of 0, desyncing Claude Code's SSE state
  machine — seen as tool calls that hang or come back empty.
- (`openai` flavor, streaming) A stall/timeout *after* the upstream stream opened (e.g.
  a reasoning model going silent mid-generation past the 300s read timeout) propagated
  an uncaught exception out of the generator, killing the connection with no signal to
  the client — the same "empty/interrupted turn" symptom. Now caught and surfaced as a
  clean `event: error` SSE frame, matching the `anthropic` flavor's rawstream path.
  `httpx.ReadTimeout`/`PoolTimeout`/`WriteTimeout` are also now retried against the
  fallback chain like connection errors, instead of failing immediately.

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
