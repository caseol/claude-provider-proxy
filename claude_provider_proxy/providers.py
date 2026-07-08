"""Provider configuration. A provider is a backend the proxy can route to.

Built-in defaults cover OpenCode Go (Anthropic-compatible passthrough), OpenCode Zen
and NVIDIA NIM (OpenAI-compatible translation). Override or add providers via
~/.config/claude-provider-proxy/providers.json. API keys come from the environment
(loaded from ~/.config/claude-provider-proxy/.env)."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(os.path.expanduser("~/.config/claude-provider-proxy"))
PROVIDERS_FILE = CONFIG_DIR / "providers.json"

# A browser-ish UA: OpenCode Zen sits behind Cloudflare, which 1010-blocks the
# default httpx UA. NVIDIA needs no UA.
_BROWSER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like "
               "Gecko) Chrome/138.0.0.0 Safari/537.36")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MIN_TOKENS_REASONING = 1024


@dataclass
class ProviderConfig:
    name: str
    flavor: str                      # "openai" (translate) | "anthropic" (passthrough)
    base_url: str
    api_key_env: str
    auth: str = "bearer"             # "bearer" | "x-api-key"
    user_agent: str | None = None
    reasoning_models: set[str] = field(default_factory=set)
    min_tokens_reasoning: int = MIN_TOKENS_REASONING
    cache_control_strip: list[str] = field(default_factory=list)  # model substrings
    # Body substrings that make an otherwise-fatal status (typically 400) retryable for
    # this provider only. E.g. OpenCode Go returns a generic 400 "invalid_request_error"
    # for what are actually transient backend hiccups (confirmed against oc-go-cc's own
    # logs, which retry through its fallback chain on this same error).
    transient_error_patterns: list[str] = field(default_factory=list)
    # Replay tool history natively (assistant.tool_calls + role:"tool" messages with
    # tool_call_id) instead of flattening it into [tool_use:]/[tool_result:] text
    # markers. Only enable for backends verified to accept role:"tool" — markers stay
    # the safe default for unknown OpenAI-compatible endpoints.
    native_tool_history: bool = False
    # Extra top-level request fields injected when the client asks for extended
    # thinking (Anthropic `thinking: {"type": "enabled"}`) AND the model is in
    # reasoning_models — each backend names its reasoning knob differently, e.g.
    # NVIDIA NIM DeepSeek: {"chat_template_kwargs": {"thinking": true}}.
    reasoning_extra_body: dict = field(default_factory=dict)
    # Headers HTTP extras enviados em todo request a este provider (ex. atribuição
    # do OpenRouter: HTTP-Referer / X-Title). Mesclados por cima dos headers base.
    extra_headers: dict = field(default_factory=dict)
    fallbacks: dict[str, list[str]] = field(default_factory=dict)  # model -> chain
    default_fallback: list[str] = field(default_factory=list)
    default_model: str | None = None

    @property
    def api_key(self) -> str:
        return os.environ.get(self.api_key_env, "")

    def chain_for(self, model: str) -> list[str]:
        """Ordered model chain to try: [model, *fallbacks]. De-duped, model first."""
        chain = [model]
        for m in self.fallbacks.get(model, self.default_fallback):
            if m and m not in chain:
                chain.append(m)
        return chain

    def strip_cache_control_for(self, model: str) -> bool:
        m = (model or "").lower()
        return any(sub.lower() in m for sub in self.cache_control_strip)

    def matches_transient_pattern(self, body_text: str) -> bool:
        return any(p in (body_text or "") for p in self.transient_error_patterns)


BUILTIN: dict[str, dict] = {
    "opencode-go": {
        # OpenAI-compatible flavor (like the original oc-go-cc setup): OpenCode Go's
        # native Anthropic /messages endpoint mistranslates tools to Moonshot/kimi
        # ("function name is invalid"), so we translate and hit /chat/completions.
        "flavor": "openai",
        "base_url": "https://opencode.ai/zen/go/v1",
        "api_key_env": "OC_GO_CC_API_KEY",
        "auth": "bearer",
        "reasoning_models": ["kimi-k2.7-code", "qwen3.7-max", "qwen3.7-plus",
                             "deepseek-v4-flash", "deepseek-v4-pro", "glm-5.2"],
        "default_model": "kimi-k2.7-code",
        "transient_error_patterns": ["Upstream request failed"],
        # Verified live (2026-07-05): the Go gateway accepts assistant.tool_calls +
        # role:"tool" history replay (kimi answered from the tool result).
        "native_tool_history": True,
    },
    "opencode-zen": {
        # native_tool_history stays off: verified live (2026-07-05) that the Zen
        # gateway deterministically 400s ("Upstream request failed") on role:"tool"
        # messages, while the same request with text markers succeeds.
        "flavor": "openai",
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "auth": "bearer",
        "user_agent": _BROWSER_UA,
        "reasoning_models": ["deepseek-v4-flash-free", "deepseek-v4-pro", "deepseek-v4-flash"],
        # deepseek-v4-flash-free always reasons (no disable knob accepted; verified
        # 2026-07-05) and its chain-of-thought grows with context size — with the
        # default 1024 floor, heavy agentic turns exhaust the budget mid-thought and
        # return empty content. Tokens are free on Zen; give reasoning real room.
        "min_tokens_reasoning": 4096,
        "default_model": "deepseek-v4-flash-free",
        # Universal safety net: Zen's other free-tier models (nemotron-3-ultra-free,
        # hy3-free, mimo-v2.5-free, north-mini-code-free, ...) had NO fallback chain at
        # all until this was added — a single 429/5xx on any of them ended the turn
        # outright ("model temporarily unavailable"), which is what starved the auto
        # mode safety classifier during an unstable-model session (2026-07-07). Route
        # any unlisted model to deepseek-v4-flash-free, the one Zen model with a
        # verified-stable, tuned reasoning floor.
        "default_fallback": ["deepseek-v4-flash-free"],
    },
    "nvidia": {
        "flavor": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "auth": "bearer",
        # step-3.7-flash reasons before answering (reasoning_content); without the
        # token floor it burns small max_tokens budgets entirely on reasoning and
        # returns empty content.
        "reasoning_models": ["deepseek-ai/deepseek-v4-flash", "deepseek-ai/deepseek-v4-pro",
                             "stepfun-ai/step-3.7-flash"],
        "default_model": "deepseek-ai/deepseek-v4-flash",
        # Verified live (2026-07-05): NIM accepts native tool history (503s seen
        # during probing were the usual ResourceExhausted capacity flakiness).
        "native_tool_history": True,
    },
    "openrouter": {
        # OpenRouter: agregador OpenAI-compatível (centenas de modelos, uma chave).
        "flavor": "openai",
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "auth": "bearer",
        # Headers de atribuição recomendados pelo OpenRouter (aparecem no ranking
        # público e nos logs da conta). Opcionais para funcionar, mas boa prática.
        "extra_headers": {
            "HTTP-Referer": "https://github.com/claude-provider-proxy",
            "X-Title": "claude-provider-proxy",
        },
        # DeepSeek v4 no OpenRouter suporta reasoning (supported_parameters: reasoning,
        # effort xhigh/high). O knob unificado do OpenRouter é o campo `reasoning`.
        "reasoning_models": ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro"],
        "reasoning_extra_body": {"reasoning": {"enabled": True}},
        "default_model": "deepseek/deepseek-v4-flash",
        # Fallback robusto: os dois DeepSeek são reserva um do outro, e default_fallback
        # é a rede de segurança universal para qualquer slug fora do dict (ex. slots
        # OPUS/SONNET/HAIKU de um profile) — sem ele, um 429/503 encerraria o turno.
        "fallbacks": {
            "deepseek/deepseek-v4-flash": ["deepseek/deepseek-v4-pro"],
            "deepseek/deepseek-v4-pro": ["deepseek/deepseek-v4-flash"],
        },
        "default_fallback": ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro"],
        # native_tool_history desligado até verificar ao vivo o aceite de role:"tool".
    },
}


def _make(name: str, d: dict) -> ProviderConfig:
    return ProviderConfig(
        name=name,
        flavor=d.get("flavor", "openai"),
        base_url=d["base_url"].rstrip("/"),
        api_key_env=d["api_key_env"],
        auth=d.get("auth", "bearer"),
        user_agent=d.get("user_agent"),
        reasoning_models=set(d.get("reasoning_models", [])),
        min_tokens_reasoning=int(d.get("min_tokens_reasoning", MIN_TOKENS_REASONING)),
        cache_control_strip=list(d.get("cache_control_strip", [])),
        transient_error_patterns=list(d.get("transient_error_patterns", [])),
        native_tool_history=bool(d.get("native_tool_history", False)),
        reasoning_extra_body=dict(d.get("reasoning_extra_body", {})),
        extra_headers=dict(d.get("extra_headers", {})),
        fallbacks={k: list(v) for k, v in d.get("fallbacks", {}).items()},
        default_fallback=list(d.get("default_fallback", [])),
        default_model=d.get("default_model"),
    )


def load_providers() -> dict[str, ProviderConfig]:
    """Built-in defaults, deep-merged with providers.json overrides/additions."""
    merged = {k: dict(v) for k, v in BUILTIN.items()}
    if PROVIDERS_FILE.exists():
        try:
            user = json.loads(PROVIDERS_FILE.read_text())
        except Exception:  # noqa: BLE001
            user = {}
        for name, d in user.items():
            # "_comment"-style metadata keys (see providers.example.json) aren't providers.
            if name.startswith("_") or not isinstance(d, dict):
                continue
            merged.setdefault(name, {}).update(d)
    return {name: _make(name, d) for name, d in merged.items()}
