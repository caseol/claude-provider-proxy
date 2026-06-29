# Contributing

```bash
python3 -m pip install -r requirements.txt pytest
PYTHONPATH=. python3 -m pytest tests/ -q          # offline, no network
./bin/claude-provider-proxy daemon start          # runs from the repo
```

Guidelines:
- Keep it dependency-light (FastAPI/uvicorn/httpx) and `127.0.0.1`-only.
- The translation core (`translate_openai.py`) is the heart — add a unit test for any new
  request/response shape you handle.
- New providers should be expressible purely as config (`ProviderConfig`); avoid
  per-provider code paths.
- No secrets, keys, or `.env` files in commits.
