"""Sincroniza modelos dos perfis e fallbacks com o catálogo vivo de cada provider.

Uso:
    python -m claude_provider_proxy.sync_models [provider] [--fix]

Exemplos:
    claude-proxy sync-models                 # todos os providers
    claude-proxy sync-models land            # só provider land
    claude-proxy sync-models --fix           # propõe correções interativas
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from pathlib import Path

import httpx

from . import config
from .providers import CONFIG_DIR, ProviderConfig, load_providers, PROVIDERS_FILE
from .proxy_core import _headers

SLOTS = ["FABLE_MODEL", "OPUS_MODEL", "SONNET_MODEL", "HAIKU_MODEL", "SUBAGENT_MODEL"]
PROFILES_DIR = CONFIG_DIR / "profiles"


def _get(provider: ProviderConfig) -> dict:
    """Busca /models diretamente no upstream com a chave do provider."""
    headers = _headers(provider, anthropic=(provider.flavor == "anthropic"))
    # httpx inclui Accept */*; removemos para evitar conflitos com alguns gateways.
    headers.pop("accept", None)
    try:
        with httpx.Client(timeout=30.0) as c:
            r = c.get(f"{provider.base_url}/models", headers=headers)
        if r.status_code != 200:
            return {
                "ok": False,
                "status": r.status_code,
                "error": r.text[:300],
            }
        data = r.json()
        ids = [m["id"] for m in data.get("data", []) if m.get("id")]
        return {"ok": True, "models": sorted(set(ids))}
    except httpx.HTTPError as e:
        return {"ok": False, "error": f"HTTP error: {e}"}
    except json.JSONDecodeError as e:
        return {"ok": False, "error": f"JSON decode error: {e}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


def _parse_profile(path: Path) -> dict[str, str]:
    """Lê um arquivo .env de perfil e retorna {slot: modelo}."""
    slots: dict[str, str] = {}
    if not path.exists():
        return slots
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip()
        if k in SLOTS and v:
            slots[k] = v
    return slots


def _write_profile(path: Path, slots: dict[str, str]) -> None:
    """Reescreve o perfil preservando comentários e ordem, atualizando só slots."""
    original = path.read_text().splitlines() if path.exists() else []
    out_lines: list[str] = []
    written: set[str] = set()

    for line in original:
        stripped = line.strip()
        if stripped.startswith("#") or "=" not in stripped:
            out_lines.append(line)
            continue
        k, _ = stripped.split("=", 1)
        k = k.strip()
        if k in SLOTS and k in slots:
            out_lines.append(f"{k}={slots[k]}")
            written.add(k)
        else:
            out_lines.append(line)

    # Adiciona slots que ainda não existiam no arquivo
    for slot in SLOTS:
        if slot in slots and slot not in written:
            out_lines.append(f"{slot}={slots[slot]}")

    path.write_text("\n".join(out_lines).rstrip() + "\n")


def _pick_replacement(model: str, available: list[str], default: str | None) -> str | None:
    """Tenta achar um modelo substituto razoável.

    Heurística:
      1. mantém a mesma família (prefixo antes de / ou -)
      2. mantém o mesmo provider/sufixo (ex: :cloud)
      3. usa o default do provider
      4. primeiro modelo disponível
    """
    if model in available:
        return model
    if default and default in available:
        return default

    # família por namespace (openrouter) ou prefixo
    family = model.split("/")[0] if "/" in model else re.split(r"[-_]|:", model)[0]
    suffix = model.split(":")[-1] if ":" in model else ""

    candidates: list[tuple[int, str]] = []
    for m in available:
        if suffix and m.endswith(f":{suffix}"):
            candidates.append((0, m))  # melhor match
        elif "/" in m and m.startswith(family + "/"):
            candidates.append((1, m))
        elif m.startswith(family):
            candidates.append((2, m))

    if candidates:
        candidates.sort(key=lambda x: (x[0], x[1]))
        return candidates[0][1]
    return None


def _load_providers_json() -> dict:
    """Lê providers.json como dict; retorna {} se não existir ou for inválido."""
    if not PROVIDERS_FILE.exists():
        return {}
    try:
        return json.loads(PROVIDERS_FILE.read_text())
    except Exception:  # noqa: BLE001
        return {}


def _save_providers_json(data: dict) -> None:
    """Salva providers.json com indentação legível."""
    PROVIDERS_FILE.parent.mkdir(parents=True, exist_ok=True)
    PROVIDERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def _clean_chain(chain: list[str], available: list[str]) -> list[str]:
    """Remove modelos indisponíveis e duplicados de uma fallback chain."""
    seen: set[str] = set()
    out: list[str] = []
    for m in chain:
        if m in available and m not in seen:
            out.append(m)
            seen.add(m)
    return out


def _clean_fallbacks(
    fallbacks: dict[str, list[str]], available: list[str], default: str | None
) -> tuple[dict[str, list[str]], dict[str, str]]:
    """Limpa fallbacks inválidos. Retorna (novo_dict, mapa_de_substituições).

    Substituições mapeiam primary_model -> novo_primary_model quando o original
    não está disponível.
    """
    cleaned: dict[str, list[str]] = {}
    replacements: dict[str, str] = {}
    for primary, chain in fallbacks.items():
        new_primary = primary
        if primary not in available:
            # substitui pelo modelo mais próximo no catálogo geral
            replacement = _pick_replacement(primary, available, default)
            if replacement:
                new_primary = replacement
                replacements[primary] = replacement
            else:
                # não há substituto possível, descarta essa entry
                continue

        new_chain = _clean_chain(chain, available)
        # garante que o primary não se repita na chain
        new_chain = [m for m in new_chain if m != new_primary]

        if not new_chain:
            # sem modelos de reserva, a entry não tem utilidade
            continue

        if new_primary in cleaned:
            # mescla chains se houver colisão após renomeação
            existing = set(cleaned[new_primary])
            cleaned[new_primary].extend(m for m in new_chain if m not in existing)
        else:
            cleaned[new_primary] = new_chain

    return cleaned, replacements


def _check_fallbacks(
    name: str, provider: ProviderConfig, available: list[str]
) -> tuple[int, dict[str, list[str]] | None, list[str] | None]:
    """Verifica fallbacks e default_fallback do provider.

    Retorna (problemas, fallbacks_corrigidos_ou_None, default_fallback_corrigido_ou_None).
    """
    problems = 0
    cleaned_fallbacks: dict[str, list[str]] | None = None
    cleaned_default: list[str] | None = None

    if provider.fallbacks:
        cleaned_fallbacks, _ = _clean_fallbacks(provider.fallbacks, available, provider.default_model)
        if cleaned_fallbacks != provider.fallbacks:
            problems += 1

    if provider.default_fallback:
        cleaned_default = _clean_chain(provider.default_fallback, available)
        if cleaned_default != provider.default_fallback:
            problems += 1

    return problems, cleaned_fallbacks, cleaned_default


def _report_fallbacks(
    name: str,
    provider: ProviderConfig,
    available: list[str],
    fix: bool,
) -> int:
    """Imprime relatório de fallbacks e opcionalmente corrige providers.json."""
    problems, cleaned_fallbacks, cleaned_default = _check_fallbacks(
        name, provider, available
    )

    if not problems:
        print("  🔗  fallbacks: OK")
        return 0

    print("  🔗  fallbacks:")
    if provider.fallbacks:
        for primary, chain in provider.fallbacks.items():
            invalid = [m for m in chain if m not in available]
            primary_ok = primary in available
            if primary_ok and not invalid:
                print(f"      ✅  {primary} -> {', '.join(chain)}")
            else:
                marker = "❌" if not primary_ok else "⚠️"
                details = []
                if not primary_ok:
                    replacement = _pick_replacement(primary, chain, provider.default_model)
                    if not replacement:
                        replacement = _pick_replacement(primary, available, provider.default_model)
                    details.append(f"primary inválido{f' → {replacement}' if replacement else ''}")
                if invalid:
                    details.append(f"inválidos na chain: {', '.join(invalid)}")
                print(f"      {marker}  {primary} -> {', '.join(chain)}  ({'; '.join(details)})")

    if provider.default_fallback:
        invalid = [m for m in provider.default_fallback if m not in available]
        if invalid:
            print(f"      ❌  default_fallback: {', '.join(provider.default_fallback)}  (inválidos: {', '.join(invalid)})")

    if fix and (cleaned_fallbacks is not None or cleaned_default is not None):
        changes: dict[str, object] = {}
        if cleaned_fallbacks is not None:
            changes["fallbacks"] = cleaned_fallbacks
        if cleaned_default is not None:
            changes["default_fallback"] = cleaned_default
        if _confirm_changes(PROVIDERS_FILE, {str(k): str(v) for k, v in changes.items()}, "correções nas fallbacks"):
            data = _load_providers_json()
            data.setdefault(name, {})
            if cleaned_fallbacks is not None:
                data[name]["fallbacks"] = cleaned_fallbacks
            if cleaned_default is not None:
                data[name]["default_fallback"] = cleaned_default
            _save_providers_json(data)
            print("      ✏️   providers.json atualizado")
        else:
            print("      ⏭️   fallbacks ignoradas")

    return problems


def _report_provider(name: str, provider: ProviderConfig, fix: bool) -> int:
    """Imprime relatório de um provider. Retorna número de problemas encontrados."""
    print(f"\n{'=' * 60}")
    print(f"Provider: {name}  ({provider.base_url})")
    print(f"{'=' * 60}")

    catalog = _get(provider)
    if not catalog["ok"]:
        print(f"  ❌  não foi possível listar modelos: {catalog.get('error')}")
        return 1

    available = catalog["models"]
    print(f"  📦  {len(available)} modelo(s) disponível(eis):")
    for m in available:
        print(f"      • {m}")

    problems = _report_fallbacks(name, provider, available, fix)

    pdir = PROFILES_DIR / name
    if not pdir.exists():
        print("  ⚠️   nenhum perfil encontrado")
        return problems

    env_files = sorted(pdir.glob("*.env"))
    if not env_files:
        print("  ⚠️   nenhum perfil encontrado")
        return problems

    active = ""
    active_file = pdir / "active_profile"
    if active_file.exists():
        active = active_file.read_text().strip()

    for env_path in env_files:
        pname = env_path.stem
        mark = "  << ativo" if pname == active else ""
        print(f"\n  📝  perfil: {pname}{mark}")
        slots = _parse_profile(env_path)
        if not slots:
            print("      (sem slots definidos)")
            continue

        changes: dict[str, str] = {}
        for slot in SLOTS:
            model = slots.get(slot)
            if not model:
                print(f"      ⚠️   {slot}: não definido")
                continue
            if model in available:
                print(f"      ✅  {slot}: {model}")
            else:
                problems += 1
                replacement = _pick_replacement(
                    model, available, provider.default_model
                )
                print(
                    f"      ❌  {slot}: {model}  (não disponível"
                    f"{f' → sugestão: {replacement}' if replacement else ''})"
                )
                if fix and replacement:
                    changes[slot] = replacement

        if fix and changes:
            if _confirm_changes(env_path, changes):
                updated = {**slots, **changes}
                _write_profile(env_path, updated)
                print("      ✏️   perfil atualizado")
            else:
                print("      ⏭️   alterações ignoradas")

    return problems


def _confirm_changes(path: Path, changes: dict[str, str], label: str = "correções") -> bool:
    print(f"      Aplicar {label}?")
    for slot, model in changes.items():
        print(f"        {slot} -> {model}")
    try:
        ans = input("      [s/N] ").strip().lower()
    except EOFError:
        print()
        return False
    return ans in ("s", "sim", "y", "yes")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verifica se os modelos dos perfis e fallbacks ainda existem no catálogo de cada provider."
    )
    parser.add_argument(
        "provider",
        nargs="?",
        help="provider específico (padrão: todos)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="propõe correções interativas para modelos inválidos",
    )
    args = parser.parse_args(argv)

    config.load_env()
    providers = load_providers()

    if args.provider and args.provider not in providers:
        print(f"Provider desconhecido: {args.provider}", file=sys.stderr)
        print(f"Providers disponíveis: {', '.join(providers)}", file=sys.stderr)
        return 1

    names = [args.provider] if args.provider else list(providers.keys())
    total_problems = 0

    for name in names:
        total_problems += _report_provider(name, providers[name], fix=args.fix)

    print(f"\n{'=' * 60}")
    if total_problems == 0:
        print("Nenhum problema encontrado.")
    else:
        print(f"Total de problemas encontrados: {total_problems}")
        if not args.fix:
            print("Execute com --fix para corrigir interativamente.")

    return 1 if total_problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
