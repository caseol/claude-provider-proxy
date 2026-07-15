# Providers

A **provider** is a backend the proxy routes to. Built-ins: `opencode-go`,
`opencode-zen`, `nvidia`, `openrouter`, `groq`. Add/override in `~/.config/claude-provider-proxy/providers.json`
(deep-merged over the built-ins). Keys come from `~/.config/claude-provider-proxy/.env`.

## ProviderConfig fields

| Field | Meaning |
|---|---|
| `flavor` | `"openai"` (translate Anthropic↔OpenAI) or `"anthropic"` (passthrough to `/messages`) |
| `base_url` | Upstream base. `openai` calls `{base}/chat/completions`; `anthropic` calls `{base}/messages` |
| `api_key_env` | Env var holding the key (read from `.env`) |
| `auth` | `"bearer"` → `Authorization: Bearer <key>`; `"x-api-key"` → `x-api-key: <key>` |
| `user_agent` | Optional UA header (OpenCode Zen needs a browser UA to pass Cloudflare) |
| `reasoning_models` | Models whose `max_tokens` is floored to `min_tokens_reasoning` (1024) so thinking doesn't starve the answer |
| `cache_control_strip` | List of model-name substrings; for matches, `cache_control` is stripped before forwarding (kimi rejects it) |
| `transient_error_patterns` | List of response-body substrings that make an otherwise-fatal status (typically `400`) retryable for **this provider only** — e.g. `["Upstream request failed"]` for `opencode-go` |
| `native_tool_history` | Replay tool history natively as `assistant.tool_calls` + `role:"tool"` messages instead of text markers. Enable only for backends verified to accept `role:"tool"`; markers stay the safe default |
| `reasoning_extra_body` | Extra top-level request fields injected when the client requests extended thinking and the model is in `reasoning_models` — e.g. `{"chat_template_kwargs": {"thinking": true}}` for NVIDIA NIM DeepSeek, `{"reasoning": {"enabled": true}}` for OpenRouter |
| `model_extra_body` | `{model: {field: value}}` — extra fields injected into **every** request for that specific model, unconditionally (unlike `reasoning_extra_body`, not gated on the client requesting extended thinking). For a model whose default behavior needs suppressing regardless of client intent — e.g. Groq `qwen/qwen3.6-27b` leaks raw `<think>` tags into `content` unless `reasoning_effort:"none"` is always sent |
| `extra_headers` | Extra HTTP headers sent on every request to this provider, merged over the base headers — e.g. OpenRouter attribution `{"HTTP-Referer": "...", "X-Title": "..."}` |
| `fallbacks` | `{model: [chain…]}` — models to try after the requested one on overload/quota/5xx |
| `default_fallback` | Chain used when a requested model isn't in `fallbacks` |
| `default_model` | Used when a request omits `model` |

## Built-in defaults

```jsonc
"opencode-go":  { flavor:"openai", base_url:"https://opencode.ai/zen/go/v1",
                  api_key_env:"OC_GO_CC_API_KEY", auth:"bearer",
                  reasoning_models:["kimi-k2.7-code","qwen3.7-max","qwen3.7-plus","deepseek-v4-flash","deepseek-v4-pro","glm-5.2"],
                  transient_error_patterns:["Upstream request failed"],
                  native_tool_history:true }   // verified: gateway accepts role:"tool"
"opencode-zen": { flavor:"openai", base_url:"https://opencode.ai/zen/v1",
                  api_key_env:"ZEN_API_KEY", auth:"bearer", user_agent:"<browser UA>",
                  reasoning_models:["deepseek-v4-flash-free","deepseek-v4-pro","deepseek-v4-flash"],
                  default_fallback:["deepseek-v4-flash-free"] }
                  // native_tool_history off: the Zen gateway 400s on role:"tool" (verified) — markers only
                  // default_fallback covers Zen's other free models (nemotron-3-ultra-free, hy3-free, ...)
"nvidia":       { flavor:"openai", base_url:"https://integrate.api.nvidia.com/v1",
                  api_key_env:"NVIDIA_API_KEY", auth:"bearer",
                  reasoning_models:["deepseek-ai/deepseek-v4-flash","deepseek-ai/deepseek-v4-pro"],
                  native_tool_history:true }
"openrouter":   { flavor:"openai", base_url:"https://openrouter.ai/api/v1",
                  api_key_env:"OPENROUTER_API_KEY", auth:"bearer",
                  extra_headers:{"HTTP-Referer":"...","X-Title":"claude-provider-proxy"},
                  reasoning_models:["deepseek/deepseek-v4-flash","deepseek/deepseek-v4-pro"],
                  reasoning_extra_body:{"reasoning":{"enabled":true}},
                  default_model:"deepseek/deepseek-v4-flash",
                  fallbacks:{"deepseek/deepseek-v4-flash":["deepseek/deepseek-v4-pro"],
                             "deepseek/deepseek-v4-pro":["deepseek/deepseek-v4-flash"]},
                  default_fallback:["deepseek/deepseek-v4-flash","deepseek/deepseek-v4-pro"] }
                  // native_tool_history off until role:"tool" acceptance is verified live
"groq":         { flavor:"openai", base_url:"https://api.groq.com/openai/v1",
                  api_key_env:"GROQ_API_KEY", auth:"bearer",
                  reasoning_models:["openai/gpt-oss-120b","openai/gpt-oss-20b"],
                  reasoning_extra_body:{"reasoning_effort":"high","include_reasoning":true},
                  model_extra_body:{"qwen/qwen3.6-27b":{"reasoning_effort":"none"}},
                  default_model:"openai/gpt-oss-120b",
                  native_tool_history:true,
                  fallbacks:{"openai/gpt-oss-120b":["llama-3.3-70b-versatile","openai/gpt-oss-20b","llama-3.1-8b-instant"],
                             "llama-3.3-70b-versatile":["openai/gpt-oss-120b","llama-3.1-8b-instant"],
                             "openai/gpt-oss-20b":["llama-3.1-8b-instant"]},
                  default_fallback:["llama-3.3-70b-versatile","llama-3.1-8b-instant"] }
                  // Groq's reasoning knob is flat (reasoning_effort/include_reasoning), unlike
                  // OpenRouter's nested {"reasoning":{"enabled":true}} — only gpt-oss models
                  // support it cleanly (reasoning in a dedicated field, never inline).
                  // moonshotai/kimi-k2-instruct-0905 is documented by Groq but returned
                  // "model_not_found" live (2026-07-15) on this account — not used.
                  // qwen/qwen3.6-27b (used for the Fable slot) leaks raw <think> tags into
                  // content by default (reasoning always on, format "raw") — model_extra_body
                  // forces reasoning_effort:"none" for it on every request, verified live
                  // (2026-07-15): clean content, normal tool calling, no extra token cost.
                  // qwen/qwen3-32b has the same leak and isn't mapped to any slot (would need
                  // the same treatment before use).
                  // native_tool_history:true — verified live (2026-07-15): Groq accepts
                  // assistant.tool_calls + role:"tool" natively.
```

## Adding a provider

```json
{
  "my-llm": {
    "flavor": "openai",
    "base_url": "https://api.example.com/v1",
    "api_key_env": "MY_LLM_KEY",
    "auth": "bearer"
  }
}
```
Then `MY_LLM_KEY=...` in `.env`, and `claude-proxy my-llm`. The proxy serves it at
`http://127.0.0.1:3460/my-llm/v1/messages`.

## Fallback chains

On a connection error or a retryable status (`413, 429, 500, 502, 503, 504`), the proxy
tries the next model in `chain_for(model) = [model, *fallbacks]`. A `400` is also retried
if the provider's `transient_error_patterns` matches the response body — otherwise a `400`
(e.g. "model not supported") is returned as-is. Fallback is for overload/quota/known-transient
upstream quirks, not genuine bad requests.

`413` is included because Groq maps its per-model tokens-per-minute cap to `413 Request
too large` (`code: "rate_limit_exceeded"`, `type: "tokens"`) instead of `429` — a
functional rate limit, not a genuinely oversized payload. Without it in the retryable set,
a low-TPM model (Groq free/on-demand tiers cap most models at 6,000–12,000 TPM) would kill
the turn on the first attempt instead of advancing toward a model with more headroom.

For streaming, fallback only applies **before** the stream starts; once bytes flow, a
mid-stream failure is surfaced as an `event: error` SSE frame.

Set `default_fallback` as a **universal safety net**: any requested model not listed in
`fallbacks` (e.g. the OPUS/SONNET/HAIKU slot slugs a profile might request) falls through
to it. Without one, a `429`/`503` on an unlisted slug ends the turn with "fallback chain
exhausted". The `openrouter` built-in ships both a per-model chain and a `default_fallback`;
`opencode-zen` ships a `default_fallback` only, so every free-tier model routes to the one
proven-stable default (`deepseek-v4-flash-free`) on a retryable error.
