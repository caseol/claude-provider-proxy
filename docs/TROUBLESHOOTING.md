# Troubleshooting

### `has_key: false` in /healthz
The key env var isn't set. Put it in `~/.config/claude-provider-proxy/.env`
(`ZEN_API_KEY=…`, etc.) and restart the daemon (`claude-proxy daemon stop && … start`).
The `.env` is loaded at daemon startup.

### `404 unknown provider`
The path segment doesn't match a configured provider. Check `GET /healthz` for the provider
names, or add it in `providers.json`.

### Cloudflare 1010 / blocked (OpenCode Zen)
Zen needs a browser `user_agent` (built into the default config). If you override the
provider, keep a browser-like `user_agent`.

### kimi returns HTTP 400 "Extra inputs… cache_control"
The provider needs `cache_control` stripped for that model. Add the model substring to the
provider's `cache_control_strip` (built-in for `opencode-go` + `kimi`).

### Empty answer from a reasoning model
Its thinking consumed the token budget. Ensure the model is listed in the provider's
`reasoning_models` (so `max_tokens` is floored to 1024), or raise `max_tokens`.

### "all models failed" / 502
Every model in the chain hit a retryable error (often a quota/usage limit on a paid
provider) or a connection failure. Check the provider's quota; switch provider/profile.

### A model error that *should* fall back doesn't
Fallback triggers on `429`/`5xx`/connection errors, and on `400`s only when the provider's
`transient_error_patterns` matches the error body (built in for `opencode-go`'s generic
"Upstream request failed"). A `400` that doesn't match any pattern is a hard client error
("model not supported", bad schema, etc.) — fix the model name, add it to a `fallbacks`
chain whose first entry is valid, or add a matching substring to that provider's
`transient_error_patterns` if you've confirmed the error is actually transient.

### Tool calls look wrong on a non-native backend
The backend must emit the `[tool_use: name id=… input={…}]` marker (or native OpenAI
`tool_calls`). Models that don't follow either format won't round-trip tools cleanly.
```
