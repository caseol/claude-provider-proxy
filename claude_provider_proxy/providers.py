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
    },
    "opencode-zen": {
        "flavor": "openai",
        "base_url": "https://opencode.ai/zen/v1",
        "api_key_env": "ZEN_API_KEY",
        "auth": "bearer",
        "user_agent": _BROWSER_UA,
        "reasoning_models": ["deepseek-v4-flash-free", "deepseek-v4-pro", "deepseek-v4-flash"],
        "default_model": "deepseek-v4-flash-free",
    },
    "nvidia": {
        "flavor": "openai",
        "base_url": "https://integrate.api.nvidia.com/v1",
        "api_key_env": "NVIDIA_API_KEY",
        "auth": "bearer",
        "reasoning_models": ["deepseek-ai/deepseek-v4-flash", "deepseek-ai/deepseek-v4-pro"],
        "default_model": "deepseek-ai/deepseek-v4-flash",
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
