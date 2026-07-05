# Providers

A **provider** is a backend the proxy routes to. Built-ins: `opencode-go`,
`opencode-zen`, `nvidia`. Add/override in `~/.config/claude-provider-proxy/providers.json`
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
| `fallbacks` | `{model: [chain…]}` — models to try after the requested one on overload/quota/5xx |
| `default_fallback` | Chain used when a requested model isn't in `fallbacks` |
| `default_model` | Used when a request omits `model` |

## Built-in defaults

```jsonc
"opencode-go":  { flavor:"openai", base_url:"https://opencode.ai/zen/go/v1",
                  api_key_env:"OC_GO_CC_API_KEY", auth:"bearer",
                  reasoning_models:["kimi-k2.7-code","qwen3.7-max","qwen3.7-plus","deepseek-v4-flash","deepseek-v4-pro","glm-5.2"],
                  transient_error_patterns:["Upstream request failed"] }
"opencode-zen": { flavor:"openai", base_url:"https://opencode.ai/zen/v1",
                  api_key_env:"ZEN_API_KEY", auth:"bearer", user_agent:"<browser UA>",
                  reasoning_models:["deepseek-v4-flash-free","deepseek-v4-pro","deepseek-v4-flash"] }
"nvidia":       { flavor:"openai", base_url:"https://integrate.api.nvidia.com/v1",
                  api_key_env:"NVIDIA_API_KEY", auth:"bearer",
                  reasoning_models:["deepseek-ai/deepseek-v4-flash","deepseek-ai/deepseek-v4-pro"] }
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

On a connection error or a retryable status (`429, 500, 502, 503, 504`), the proxy tries
the next model in `chain_for(model) = [model, *fallbacks]`. A `400` is also retried if the
provider's `transient_error_patterns` matches the response body — otherwise a `400` (e.g.
"model not supported") is returned as-is. Fallback is for overload/quota/known-transient
upstream quirks, not genuine bad requests.

For streaming, fallback only applies **before** the stream starts; once bytes flow, a
mid-stream failure is surfaced as an `event: error` SSE frame.
