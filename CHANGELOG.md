# Changelog

## [Unreleased]

### Added
- UFRJ LLM.LAND (`land`) as a built-in provider, pointing to the LiteLLM OpenAI-compatible
  proxy at `https://llm.land.ufrj.br/v1`. Uses `LAND_API_KEY`. Verified live:
  `kimi-k2.7-code-cloud` accepts native `assistant.tool_calls` + `role:"tool"` round-trips
  and emits `reasoning_content`, so `native_tool_history: true` and a reasoning-token floor
  are enabled for it. No fixed `default_model` is shipped because the available catalog
  depends on the team's virtual key; users discover it via `/v1/models` and fill a profile
  or `providers.json` override.
- Fallback chain for `land` cycling its 3 `*-cloud` models (`kimi-k2.7-code-cloud` ↔
  `glm-5.2-cloud` ↔ `minimax-m3-cloud`), matching the pattern used by `gemini`/`groq`.
  Added after a live incident (2026-08-07) where every `land` call hit 429 with
  `fallback chain exhausted` immediately — the provider had no `fallbacks`/
  `default_fallback` configured at all. Note the specific 429 seen was a per-key
  monthly usage cap ("extra usage auto reload monthly max reached"), which switching
  models can't route around; the chain still protects against per-model rate limits
  and transient 5xx on an individual backend.
- Google Gemini as a built-in provider (`gemini`), using the OpenAI-compatible endpoint
  `https://generativelanguage.googleapis.com/v1beta/openai`. Ships with model fallback
  chains (`gemini-2.5-pro` → `gemini-2.5-flash` → `gemini-2.5-flash-lite` → `gemini-2.0-flash`)
  and a proposed slot mapping (Pro for Fable/Opus, Flash for Sonnet/Subagent, Flash Lite
  for Haiku). `native_tool_history` is off by default until role:`"tool"` acceptance is
  verified live — text markers are the safe fallback.

## [0.2.0] — 2026-07-20

### Added
- `FABLE_MODEL` profile slot (`ANTHROPIC_DEFAULT_FABLE_MODEL`), matching Claude Code's new
  Fable model slot (Mythos-class tier, above Opus). `claude-proxy profile <provider> new`
  now seeds all five slots; `list`/`show` display `FABLE_MODEL` alongside the others.
- Groq as a built-in provider (`openai` flavor, native tool history), with fallback chain
  and per-model `reasoning_effort` fixes for the qwen3.6 family's raw `<think>` leakage.
- OpenRouter as a built-in provider (`openai` flavor, `extra_headers` for attribution,
  `reasoning` knob, DeepSeek + Kimi fallback chains).
- `ProviderConfig.native_tool_history_models`: per-model opt-in for native
  `tool_calls`/`role:"tool"` history replay on aggregator providers (like OpenRouter)
  that host many unrelated model families under one `native_tool_history` flag. Enabled
  for the Kimi family (`kimi-k3`, `kimi-k2.7-code`, `kimi-k2.6`, `kimi-k2.5`,
  `kimi-k2-thinking`, `kimi-k2`) on OpenRouter.

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
- (OpenRouter, Kimi family) Replaying an assistant's own past tool calls as
  `[tool_use:]`/`[tool_result:]` text markers conditioned `kimi-k2.7-code` (and the rest
  of the Kimi family) to mimic that literal marker syntax for its *own* new tool calls
  instead of emitting real `tool_calls`, reusing stale ids in the process. Claude Code's
  harness then reported `[Tool use interrupted]` on those hallucinated markers, and the
  resulting desync between "did that tool call actually run" and the real file state
  caused cascading `Edit` failures ("exact indentation must have changed") on the next
  turn. Fixed via `native_tool_history_models` above — native tool_calls/`role:"tool"`
  round-trip cleanly for the whole family, breaking the feedback loop.
- `kimi-k3`/`kimi-k2-thinking` (OpenRouter) leaked raw chain-of-thought into `content`
  without the `reasoning` param enabled, burning `max_tokens` to `finish_reason:"length"`
  and cutting the turn short. Now always requested via `model_extra_body` (kimi-k3) /
  `reasoning_models` (both), with a raised `min_tokens_reasoning` floor (3072) so tight
  budgets don't zero out `content`.
- 413 (Groq's token-per-minute cap, returned as "Request too large" instead of 429) is
  now a retryable status, advancing the fallback chain instead of killing the turn.

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
