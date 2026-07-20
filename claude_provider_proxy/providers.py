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

# 413 included alongside the classic overload/quota set: Groq returns 413 "Request
# too large" (code "rate_limit_exceeded", type "tokens") for its per-model
# tokens-per-minute cap — functionally a rate limit, just mapped to 413 instead of
# 429. Without it, a 413 on a low-TPM model killed the turn instead of advancing the
# fallback chain toward a model with more TPM headroom (verified live 2026-07-15).
RETRYABLE_STATUS = {413, 429, 500, 502, 503, 504}
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
    # Per-model opt-in on top of the provider-wide flag above — for aggregators like
    # OpenRouter that host many unrelated model families under one provider, flipping
    # native_tool_history globally would apply it to families never verified live.
    # Verified 2026-07-19: feeding kimi-k2.7-code its own PAST tool calls as
    # [tool_use:]/[tool_result:] TEXT markers conditioned it to mimic that literal
    # syntax for its OWN new tool calls (hallucinated markers with reused ids, logged
    # as "marker 'Read' reused tool_use id ..." — Claude Code's harness then reported
    # "[Tool use interrupted]" because a marker embedded in content isn't a real
    # tool_use block it can execute). Native tool_calls + role:"tool" round-tripped
    # cleanly for the whole Kimi family on OpenRouter, breaking that feedback loop.
    native_tool_history_models: set[str] = field(default_factory=set)
    # Extra top-level request fields injected when the client asks for extended
    # thinking (Anthropic `thinking: {"type": "enabled"}`) AND the model is in
    # reasoning_models — each backend names its reasoning knob differently, e.g.
    # NVIDIA NIM DeepSeek: {"chat_template_kwargs": {"thinking": true}}.
    reasoning_extra_body: dict = field(default_factory=dict)
    # Campos extras injetados incondicionalmente (independente de thinking) para
    # modelos específicos — ex. Groq qwen/qwen3.6-27b vaza tags <think> cruas no
    # content por padrão; {"reasoning_effort": "none"} desliga isso sempre, não só
    # quando o cliente pede extended thinking.
    model_extra_body: dict[str, dict] = field(default_factory=dict)
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
        # kimi-k3 e kimi-k2-thinking entraram aqui em 2026-07-18: verificado ao vivo que
        # kimi-k3 SEM o param `reasoning` vaza o chain-of-thought cru dentro de `content`
        # (texto tipo "The user just said... this is a simple greeting...", queimando
        # max_tokens até finish_reason="length" e cortando o turno — sessões kimi3 do
        # perfil openrouter ficavam interrompidas no slot Fable por causa disso).
        # kimi-k2-thinking já isola o CoT em `message.reasoning` mesmo sem o param, mas
        # herda o floor de min_tokens_reasoning abaixo para não zerar `content` com
        # budgets apertados.
        "reasoning_models": ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro",
                             "moonshotai/kimi-k3", "moonshotai/kimi-k2-thinking"],
        "reasoning_extra_body": {"reasoning": {"enabled": True}},
        # kimi-k3 sempre raciocina internamente antes de responder — sem isto, o
        # reasoning_extra_body acima só é injetado quando o cliente pede thinking
        # explicitamente (Claude Code raramente pede isso nas chamadas normais do slot
        # Fable), e o CoT continua vazando cru em content. Aplicado incondicionalmente
        # (mesmo padrão do model_extra_body da Groq para qwen3.6-27b).
        "model_extra_body": {"moonshotai/kimi-k3": {"reasoning": {"enabled": True}}},
        # kimi-k3/kimi-k2-thinking sempre gastam parte do budget pensando (mesmo padrão
        # do "always reasons" do deepseek-v4-flash-free na Zen); o floor default de 1024
        # ainda deixava content vazio em turnos com max_tokens apertado.
        "min_tokens_reasoning": 3072,
        "default_model": "deepseek/deepseek-v4-flash",
        # Fallback robusto: os dois DeepSeek são reserva um do outro; a família Kimi tem
        # sua própria cadeia (2026-07-18) para não pular direto pra um modelo de outra
        # família — que muda de "personalidade" no meio da sessão — a cada 429/503.
        # default_fallback continua a rede de segurança universal para qualquer slug
        # fora do dict (ex. slots OPUS/SONNET/HAIKU de um profile não-Kimi).
        "fallbacks": {
            "deepseek/deepseek-v4-flash": ["deepseek/deepseek-v4-pro"],
            "deepseek/deepseek-v4-pro": ["deepseek/deepseek-v4-flash"],
            "moonshotai/kimi-k3": ["moonshotai/kimi-k2.7-code", "deepseek/deepseek-v4-flash"],
            "moonshotai/kimi-k2.7-code": ["moonshotai/kimi-k2.6", "deepseek/deepseek-v4-flash"],
            "moonshotai/kimi-k2.6": ["moonshotai/kimi-k2.7-code", "deepseek/deepseek-v4-flash"],
            "moonshotai/kimi-k2.5": ["moonshotai/kimi-k2.6", "deepseek/deepseek-v4-flash"],
            "moonshotai/kimi-k2-thinking": ["moonshotai/kimi-k2.7-code", "deepseek/deepseek-v4-flash"],
            "moonshotai/kimi-k2": ["moonshotai/kimi-k2.5", "deepseek/deepseek-v4-flash"],
        },
        "default_fallback": ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro"],
        # native_tool_history global desligado: DeepSeek/Qwen/etc. não foram verificados
        # ao vivo. A família Kimi foi (2026-07-19, ver comentário no dataclass) — só ela
        # entra no opt-in per-model.
        # moonshotai/kimi-k2 (base, usado no perfil openrouter/kimi.env) entrou em
        # 2026-07-19: não suporta o knob de reasoning (fora de supported_parameters,
        # não vaza CoT), mas se beneficia da mesma correção de história nativa.
        "native_tool_history_models": ["moonshotai/kimi-k3", "moonshotai/kimi-k2.7-code",
                                       "moonshotai/kimi-k2.6", "moonshotai/kimi-k2.5",
                                       "moonshotai/kimi-k2-thinking", "moonshotai/kimi-k2"],
    },
    "groq": {
        # Groq (LPU inference, https://api.groq.com/openai/v1) — OpenAI-compatível nativo.
        "flavor": "openai",
        "base_url": "https://api.groq.com/openai/v1",
        "api_key_env": "GROQ_API_KEY",
        "auth": "bearer",
        # moonshotai/kimi-k2-instruct-0905 é documentado pela Groq mas retornou 404
        # "model_not_found" ao vivo (2026-07-15) — não disponível nesta conta apesar
        # de listado na doc pública. openai/gpt-oss-120b é o maior modelo confirmado
        # ao vivo (via /v1/models + chamada real) e vira o flagship.
        # qwen/qwen3-32b e qwen/qwen3.6-27b também disponíveis, mas vazam tags
        # <think>...</think> cruas dentro do content por padrão (reasoning_format
        # "raw" é o default deles) — poluiria a resposta do Claude Code sem um
        # reasoning_extra_body dedicado; ficam de fora do mapeamento por ora.
        # Só os modelos gpt-oss expõem reasoning limpo (campo message.reasoning
        # dedicado, nunca inline) na Groq; kimi-k2 e llama não têm o knob.
        "reasoning_models": ["openai/gpt-oss-120b", "openai/gpt-oss-20b"],
        # Knob de reasoning da Groq é plano (reasoning_effort), diferente do aninhado
        # do OpenRouter. include_reasoning:true cai no campo message.reasoning que
        # translate_openai.py já resgata (reasoning_content|reasoning).
        "reasoning_extra_body": {"reasoning_effort": "high", "include_reasoning": True},
        "default_model": "openai/gpt-oss-120b",
        "fallbacks": {
            "openai/gpt-oss-120b": ["llama-3.3-70b-versatile", "openai/gpt-oss-20b",
                                     "llama-3.1-8b-instant"],
            "llama-3.3-70b-versatile": ["openai/gpt-oss-120b", "llama-3.1-8b-instant"],
            "openai/gpt-oss-20b": ["llama-3.1-8b-instant"],
        },
        # Rede de segurança universal para qualquer slug fora do dict acima.
        "default_fallback": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant"],
        # Verificado ao vivo (2026-07-15): a Groq aceita assistant.tool_calls +
        # role:"tool" (tool_call_id) nativamente e responde de forma coerente.
        "native_tool_history": True,
        # qwen/qwen3.6-27b (usado no slot Fable) vaza tags <think>...</think> cruas
        # dentro do content por padrão (reasoning ligado sem pedido, formato "raw").
        # reasoning_effort:"none" desliga o reasoning por completo — verificado ao
        # vivo (2026-07-15): content limpo, tool calling normal, sem custo extra de
        # tokens de raciocínio. Aplicado incondicionalmente (não gated por thinking,
        # diferente de reasoning_extra_body) porque o vazamento acontece em toda
        # chamada, não só quando o cliente pede extended thinking.
        "model_extra_body": {"qwen/qwen3.6-27b": {"reasoning_effort": "none"}},
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
        native_tool_history_models=set(d.get("native_tool_history_models", [])),
        reasoning_extra_body=dict(d.get("reasoning_extra_body", {})),
        model_extra_body={k: dict(v) for k, v in d.get("model_extra_body", {}).items()},
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
