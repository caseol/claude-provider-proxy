# Translation (Anthropic ↔ OpenAI)

For `openai`-flavor providers, the proxy converts each Anthropic Messages request to an
OpenAI Chat Completions request and converts the response back. Code:
`claude_provider_proxy/translate_openai.py`.

## Request: Anthropic → OpenAI

- **system**: a string is used as-is; a **list of content blocks** is flattened to
  `"\n".join(text blocks)` (dropping `cache_control`, which OpenAI-compatible APIs reject).
- **messages**: each message's content blocks are flattened to a single string:
  - `text` → the text
  - `image` → `"[image content]"` (image data is **dropped**)
  - `tool_use` → `[tool_use: <name> id=<id> input=<json>]`
  - `tool_result` → `[tool_result: <json>]`
- **max_tokens**: default 4096; floored to 1024 for `reasoning_models` (so internal
  thinking doesn't consume the whole budget and return empty content).
- **tools** → OpenAI `tools` (`type:function`, `parameters = input_schema`).
- **stop_sequences** → `stop`. **temperature** default 0.7. **stream** → adds
  `stream_options.include_usage`.

## Response: OpenAI → Anthropic

- `message.content` → a `text` block; if it contains `[tool_use: …]` markers they are
  expanded into `tool_use` blocks (the round-trip partner of the request stringification).
- native `tool_calls` → `tool_use` blocks (arguments JSON-parsed).
- `finish_reason` → `stop_reason` (`stop→end_turn`, `length→max_tokens`,
  `tool_calls→tool_use`).
- usage mapped (`prompt_tokens→input_tokens`, `completion_tokens→output_tokens`).

### Streaming
Builds the Anthropic SSE sequence (`message_start` → `content_block_start/delta/stop` →
tool blocks → `message_delta` → `message_stop`) from the OpenAI delta stream. Native
streamed `tool_calls` are collected and emitted after the text block; for models that emit
text-marker tool calls, the accumulated text is re-parsed at the end.
`usage.output_tokens` is propagated from the final usage chunk (a fix over the originals,
which hardcoded it to 0).

## Limits / fidelity

- Images are not forwarded.
- Tool-call fidelity on non-native backends depends on the model emitting the exact
  `[tool_use: name id=… input={…}]` marker format.
- Anthropic `thinking` request blocks are not mapped to provider-specific reasoning params
  (only the `max_tokens` floor is applied).

The `anthropic`-flavor (passthrough, e.g. OpenCode Go) does **no** translation — it forwards
the Anthropic body to the upstream `/messages`, only stripping `cache_control` for
configured models.
