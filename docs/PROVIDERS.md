# Providers

A **provider** is a backend the proxy routes to. Built-ins: `opencode-go`,
`opencode-zen`, `nvidia`, `openrouter`. Add/override in `~/.config/claude-provider-proxy/providers.json`
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
                  reasoning_models:["deepseek-v4-flash-free","deepseek-v4-pro","deepseek-v4-flash"] }
                  // native_tool_history off: the Zen gateway 400s on role:"tool" (verified) — markers only
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

Set `default_fallback` as a **universal safety net**: any requested model not listed in
`fallbacks` (e.g. the OPUS/SONNET/HAIKU slot slugs a profile might request) falls through
to it. Without one, a `429`/`503` on an unlisted slug ends the turn with "fallback chain
exhausted". The `openrouter` built-in ships both a per-model chain and a `default_fallback`.
