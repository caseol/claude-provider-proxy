"""Offline unit tests for sync_models (no network)."""
import sys
import os
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from claude_provider_proxy import sync_models as sm          # noqa: E402
from claude_provider_proxy.providers import ProviderConfig   # noqa: E402


def test_parse_profile_reads_slots(tmp_path):
    pf = tmp_path / "x.env"
    pf.write_text("""
# comment
FABLE_MODEL=a
OPUS_MODEL=b
SONNET_MODEL=c
HAIKU_MODEL=d
SUBAGENT_MODEL=e
OTHER=value
""")
    assert sm._parse_profile(pf) == {
        "FABLE_MODEL": "a",
        "OPUS_MODEL": "b",
        "SONNET_MODEL": "c",
        "HAIKU_MODEL": "d",
        "SUBAGENT_MODEL": "e",
    }


def test_parse_profile_missing_file():
    assert sm._parse_profile(Path("/nonexistent")) == {}


def test_write_profile_preserves_comments_and_order(tmp_path):
    pf = tmp_path / "x.env"
    pf.write_text("# header\nFABLE_MODEL=old\n# mid\nOPUS_MODEL=old\n")
    sm._write_profile(pf, {
        "FABLE_MODEL": "new",
        "OPUS_MODEL": "new",
        "SONNET_MODEL": "s",
        "HAIKU_MODEL": "h",
        "SUBAGENT_MODEL": "u",
    })
    text = pf.read_text()
    assert "# header" in text
    assert "# mid" in text
    assert "FABLE_MODEL=new" in text
    assert "OPUS_MODEL=new" in text
    assert "SONNET_MODEL=s" in text


def test_pick_replacement_prefers_family_and_suffix():
    available = ["deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro",
                 "moonshotai/kimi-k3", "kimi-k2.7-code-cloud"]
    # mesmo provider/sufixo
    assert sm._pick_replacement("deepseek/deepseek-v4-zzz", available, None) \
        == "deepseek/deepseek-v4-flash"
    # mesma família de slug simples
    assert sm._pick_replacement("kimi-k2.7-code-cloud", available, None) \
        == "kimi-k2.7-code-cloud"
    # fallback para default quando disponível
    assert sm._pick_replacement("missing", available, "moonshotai/kimi-k3") \
        == "moonshotai/kimi-k3"
    # sem match e sem default -> None
    assert sm._pick_replacement("foo", available, "not-there") is None


def test_pick_replacement_keeps_valid_model():
    available = ["a", "b"]
    assert sm._pick_replacement("a", available, "b") == "a"


def test_confirm_changes_accepts_yes(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "s\n")
    assert sm._confirm_changes(Path("x"), {"FABLE_MODEL": "a"}) is True


def test_confirm_changes_rejects_no(monkeypatch):
    monkeypatch.setattr("builtins.input", lambda _: "n\n")
    assert sm._confirm_changes(Path("x"), {"FABLE_MODEL": "a"}) is False


def test_confirm_changes_eof_returns_false(monkeypatch):
    def _eof(_):
        raise EOFError()
    monkeypatch.setattr("builtins.input", _eof)
    assert sm._confirm_changes(Path("x"), {"FABLE_MODEL": "a"}) is False


def test_clean_chain_keeps_available_only():
    assert sm._clean_chain(["a", "b", "c"], ["a", "c"]) == ["a", "c"]
    assert sm._clean_chain(["a", "a", "b"], ["a", "b"]) == ["a", "b"]
    assert sm._clean_chain(["x", "y"], ["a"]) == []


def test_clean_fallbacks_removes_invalid_and_substitutes_primary():
    available = ["new-a", "b", "c"]
    fallbacks = {
        "old-a": ["b", "x"],           # primary inválido, chain parcialmente válida
        "b": ["c", "y"],               # primary válido, chain parcialmente válida
    }
    cleaned, repl = sm._clean_fallbacks(fallbacks, available, default="new-a")
    assert repl == {"old-a": "new-a"}
    assert cleaned == {"new-a": ["b"], "b": ["c"]}


def test_clean_fallbacks_discards_empty_chains():
    available = ["b"]
    fallbacks = {"missing": ["x"], "b": []}
    cleaned, repl = sm._clean_fallbacks(fallbacks, available, default=None)
    assert repl == {}
    assert cleaned == {}


def test_load_and_save_providers_json(tmp_path, monkeypatch):
    cfg = tmp_path / "providers.json"
    monkeypatch.setattr(sm, "PROVIDERS_FILE", cfg)
    assert sm._load_providers_json() == {}
    sm._save_providers_json({"land": {"default_model": "x"}})
    assert sm._load_providers_json() == {"land": {"default_model": "x"}}
