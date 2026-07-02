"""Entrypoint: uvicorn on 127.0.0.1."""
from __future__ import annotations

import logging

import uvicorn

from . import config


def main() -> None:
    config.load_env()
    logging.basicConfig(level=logging.WARNING,
                         format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    print(f"claude-provider-proxy on http://{config.HOST}:{config.PORT}")
    print("point Claude Code at  ANTHROPIC_BASE_URL=http://127.0.0.1:"
          f"{config.PORT}/<provider>")
    uvicorn.run("claude_provider_proxy.app:app", host=config.HOST, port=config.PORT,
                log_level="warning")


if __name__ == "__main__":
    main()
