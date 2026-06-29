# A2A integration

This proxy plugs into the agent-to-agent (A2A) setup (see the companion
*claude-session-gateway* project). The launcher exports `AGENT_LANE` so the A2A MCP shim
identifies each session by its provider.

## What the launcher does

`bin/claude-provider-proxy <provider>` sets `AGENT_LANE` to a short lane name
(`opencode-go→go`, `opencode-zen→zen`, `nvidia→nv`, otherwise the provider name) and, if
`~/.local/bin/_a2a_common.sh` exists, sources it (loading the A2A shim and exec'ing
`claude`). Otherwise it just `exec claude`.

So sessions launched through this proxy register with the broker under the right lane,
and `ask_agent`/`send_message` work as usual.

## Lane derivation note

The A2A shim also auto-derives a lane from `ANTHROPIC_BASE_URL`. With this proxy the URL is
`http://127.0.0.1:3460/<provider>` (provider in the **path**, one shared port), so the
port-based derivation no longer distinguishes providers — but `AGENT_LANE` (set explicitly
by the launcher) takes precedence, so identity is correct.

Optional hardening (companion repo): extend the shim's `_lane_from_base_url` to read the
last path segment when present, so even sessions not launched via this launcher derive the
lane from the path.

## ask_agent across providers

For the gateway's `ask_agent` resume-fork to reach a session on a given lane, the gateway's
`lanes.json` must map that lane to a launch command — e.g.
`{"zen": {"command": "claude-provider-proxy"}}` plus the provider arg, or a thin wrapper.
Async messaging (`send_message`/inbox) needs no such mapping.
