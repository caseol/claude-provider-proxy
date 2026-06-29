# HTTP API

Base: `http://127.0.0.1:3460` (default). No auth — `127.0.0.1`-only, like the upstream
proxies it replaces. (If you expose it, put your own auth in front.)

### `GET /healthz`
```json
{ "ok": true, "port": 3460,
  "providers": { "opencode-zen": {"flavor":"openai","has_key":true}, ... } }
```

### `POST /{provider}/v1/messages`
The Anthropic Messages API. `{provider}` selects the backend. Body is a standard Anthropic
request (`model`, `max_tokens`, `messages`, optional `system`, `tools`, `stop_sequences`,
`temperature`, `stream`). Returns an Anthropic Messages response, or `text/event-stream`
when `stream: true`.

Unknown provider → `404` with an Anthropic-style error envelope.

This is the endpoint Claude Code hits, via `ANTHROPIC_BASE_URL=http://127.0.0.1:3460/{provider}`.

### `GET /{provider}/v1/models`
Passthrough of the upstream `{base}/models` listing.

## Errors

Failures use the Anthropic error envelope:
```json
{ "type": "error", "error": { "type": "api_error", "message": "..." } }
```
`502` for connection failures / exhausted fallback chain; upstream hard errors (e.g. `400`)
are passed through with their status.
