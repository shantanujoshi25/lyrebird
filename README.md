# lyrebird

Computer-use automation: an LLM **discovers** a legacy-UI flow once, the successful run
becomes a typed, versioned **Capability** artifact, and that artifact **replays
deterministically** without the LLM in the loop. Safety (allowlist + redaction), evidence,
and human handoff wrap all three.

> The through-line: **the model discovers; the artifact becomes a reusable capability;
> deterministic replay is how an agent invokes it in production.**

## Setup

```sh
uv sync                          # base deps (mock app, schema, policy)
uv sync --extra surface --extra llm --extra redaction   # + Playwright, Anthropic, Pillow
uv run playwright install chromium
cp .env.example .env             # then paste your ANTHROPIC_API_KEY (only needed for discovery)
```

- `LYREBIRD_MODEL` (in `.env`) selects the model; default `claude-sonnet-4-6`.
- Everything except the one discovery run is LLM-free and needs no API key.

## Demo path

Start the mock app in one terminal:

```sh
uv run uvicorn mockapp.app:app --host 127.0.0.1 --port 8000
```

In another terminal (`set -a; . ./.env; set +a` first, then
`export LYREBIRD_PARAM_PASSWORD="$MOCK_PASSWORD"`):

```sh
# 1. DISCOVERY (spends tokens): an LLM drives the mock app to read a member's balance,
#    then the trajectory is recorded into a Capability artifact.
uv run lyrebird discover --url http://127.0.0.1:8000

# 1b. Regenerate the artifact from the committed transcript WITHOUT a key (run w/o live services):
uv run python scripts/build_artifact.py

# 2. DETERMINISTIC REPLAY (no LLM) — happy path, extracts the balance:
uv run lyrebird replay lookup_savings_balance --url http://127.0.0.1:8000 \
    --param username=teller --param member_id=100001

# 3. REPLAY a business outcome (member not found -> BUSINESS_OUTCOME/NOT_FOUND, not a crash):
uv run lyrebird replay lookup_savings_balance --url http://127.0.0.1:8000 \
    --param username=teller --param member_id=999999

# 4. REPLAY a RISKY mutating flow -> ESCALATED (the Confirm step blocks, never submits):
uv run lyrebird replay open_subaccount --url http://127.0.0.1:8000 \
    --param member_id=100001 --param amount=250.00 \
    --pre-login --pre-nav /member/100001/subaccount --confirm-risky

# 5. OPERATOR handoff console (separate process): see who's in control, take, hand back.
uv run lyrebird operator list
uv run lyrebird operator take replay-escalated --as alice
uv run lyrebird operator handback replay-escalated --note "authorized manually"
```

Each run writes a redacted evidence directory under `evidence/`. Committed example runs:
`evidence/discover-lookup-*` (discovery), `evidence/replay-happy`, `evidence/replay-not-found`,
`evidence/replay-escalated`.

## Tests

```sh
uv run pytest -m "not slow"   # fast: schema, policy, redaction, evidence guards (~1s, no browser)
uv run pytest                 # full: + Playwright browser-driven tests (~1-4 min)
```

Replay, policy, schema, and handoff all run **with no LLM**. A subprocess import guard
(`tests/test_no_llm_in_replay.py`) proves the replay package can't load the LLM client.

## Layout

- `docs/` — requirements, solution outline, and the phased design (`00_UNDERSTANDING`,
  `01_ARCHITECTURE`, `02_PHASES`, `PROGRESS`).
- `mockapp/` — the intentionally hostile credit-union stand-in the agent drives.
- `src/lyrebird/` — `surface` (perceive/act seam) · `discovery` (LLM loop) · `recorder` ·
  `capability` (artifact schema) · `replay` (deterministic executor) · `policy` · `handoff`
  · `evidence`.
- `artifacts/` — committed Capability artifacts + exported JSON Schema.
- `evidence/` — committed example runs (discovery + three replays).
