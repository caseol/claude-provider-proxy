#!/usr/bin/env bash
# Talk to the proxy directly (no Claude Code needed). Daemon must be running:
#   claude-proxy daemon start
set -e
P=127.0.0.1:3460
PROVIDER="${1:-opencode-zen}"
MODEL="${2:-deepseek-v4-flash-free}"

echo "# health"
curl -s $P/healthz; echo

echo "# non-streaming"
curl -s -XPOST $P/$PROVIDER/v1/messages -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":50,\"messages\":[{\"role\":\"user\",\"content\":\"reply just: ok\"}]}"
echo

echo "# streaming (SSE)"
curl -sN -XPOST $P/$PROVIDER/v1/messages -H 'content-type: application/json' \
  -d "{\"model\":\"$MODEL\",\"max_tokens\":50,\"stream\":true,\"messages\":[{\"role\":\"user\",\"content\":\"count to 3\"}]}"
