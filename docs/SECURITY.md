# Security

- **Local only** — binds `127.0.0.1`. No auth by design (same trust model as the upstream
  proxies it replaces). If you expose it beyond localhost, front it with your own auth/TLS.
- **Keys** — all provider keys live in `~/.config/claude-provider-proxy/.env` (chmod it
  `600`). They are never logged and never committed (see `.gitignore`). The proxy injects
  the real key upstream; Claude Code only ever sends `ANTHROPIC_AUTH_TOKEN=unused`.
- **One daemon, many keys** — a single process holds all configured providers' keys. Keep
  the host trusted.
- **cache_control stripping** disables prompt caching for matched models (full input
  billing) — a correctness/cost tradeoff, since those APIs reject the field.
- **No request bodies are persisted**; only the daemon's stdout/stderr go to the log file
  (`/tmp/claude-provider-proxy.log`) at warning level.
