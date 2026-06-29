#!/usr/bin/env bash
# Deploy the launcher, seed config, and import existing keys/profiles if present.
# The daemon can also run straight from the repo (./bin/claude-provider-proxy daemon start).
set -e

REPO="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN="$HOME/.local/bin"
CFG="$HOME/.config/claude-provider-proxy"
mkdir -p "$BIN" "$CFG/profiles"

echo "CLAUDE_PROVIDER_PROXY_HOME=$REPO" > "$CFG/env"
install -m 0755 "$REPO/bin/claude-provider-proxy" "$BIN/claude-provider-proxy"

# Seed .env by importing keys from the old per-provider configs (if present).
if [ ! -f "$CFG/.env" ]; then
  {
    grep -h '^ZEN_API_KEY='     "$HOME/.config/claude-zen/.env" 2>/dev/null
    grep -h '^NVIDIA_API_KEY='  "$HOME/.config/claude-nv/.env"  2>/dev/null
    grep -h '^OC_GO_CC_API_KEY=' "$HOME/.config/oc-go-cc/.env"  2>/dev/null
  } > "$CFG/.env" || true
  chmod 600 "$CFG/.env"
  echo "criado $CFG/.env ($(grep -c '=' "$CFG/.env" 2>/dev/null || echo 0) chaves importadas) — edite se faltar alguma"
fi

# Import existing profiles (old dir -> new provider name).
import_profiles() {  # <old-config-dir> <provider>
  local src="$HOME/.config/$1/profiles" dst="$CFG/profiles/$2"
  [ -d "$src" ] || return 0
  mkdir -p "$dst"
  cp -n "$src"/*.env "$dst"/ 2>/dev/null || true
  [ -f "$HOME/.config/$1/active_profile" ] && cp -n "$HOME/.config/$1/active_profile" "$dst/active_profile" 2>/dev/null || true
}
import_profiles claude-zen opencode-zen
import_profiles claude-nv  nvidia
import_profiles oc-go-cc   opencode-go

echo
echo "Pronto. Garanta ~/.local/bin no PATH, então:"
echo "  claude-provider-proxy opencode-zen      # ou nvidia / opencode-go"
echo "  claude-provider-proxy daemon status"
