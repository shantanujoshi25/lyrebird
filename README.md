# lyrebird

Computer-use automation: an LLM discovers a legacy-UI flow once, the successful run becomes
a typed, versioned **Capability** artifact, and that artifact replays **deterministically**
without the LLM in the loop. Safety (allowlist + redaction), evidence, and human handoff
wrap all three.

> Status: under construction (see [`docs/PROGRESS.md`](docs/PROGRESS.md)). Full setup,
> demo path, and "run without live services" instructions land in phase C9. This README is
> a placeholder until then.

## Layout

- `docs/` — requirements, solution outline, and the phased design (`00_UNDERSTANDING`,
  `01_ARCHITECTURE`, `02_PHASES`, `PROGRESS`).
- `mockapp/` — the hostile credit-union stand-in the agent drives.
- `src/lyrebird/` — the automation system (built phase by phase).
- `tests/` — pytest suite; replay/policy/schema/handoff run with **no LLM**.

## Quick check (C1)

```sh
uv sync
uv run pytest tests/test_mockapp_http.py
uv run uvicorn mockapp.app:app   # then browse http://127.0.0.1:8000/login
```
