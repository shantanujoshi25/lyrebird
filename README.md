# lyrebird

Computer-use automation for legacy back-office apps with no API. An LLM **discovers** a UI
flow once from a natural-language goal; the successful run becomes a typed, versioned
**Capability** artifact; that artifact **replays deterministically** with no LLM in the loop.
Safety (allowlist + redaction), evidence, and human handoff wrap all three.

> The through-line: **the model discovers; the artifact becomes a reusable capability;
> deterministic replay is how an agent invokes it in production.**

The core idea for robustness: at discovery the LLM picks an element and *judges* whether each
output value is variable or fixed; we capture a **durable locator** straight off that DOM node
(id / name / data-\* / role+name / text / a stable label anchor). Replay re-resolves those
handles with real Playwright locators — deterministic, and it survives per-run value changes
because a variable value is anchored on its stable label, never on the value itself.

## Setup

```sh
uv sync                                                   # base deps
uv sync --extra surface --extra llm --extra redaction     # + Playwright, Anthropic, Pillow
uv run playwright install chromium
cp .env.example .env        # paste ANTHROPIC_API_KEY + MOCK_USERNAME/MOCK_PASSWORD
```

- `LYREBIRD_MODEL` (in `.env`) selects the model; default `claude-sonnet-4-6`.
- Only the one discovery run needs an API key. **Everything else is LLM-free.**
- Sensitive replay params (passwords) come from a local `secrets.yaml` — never the CLI,
  env, or the artifact. Copy the template: `cp secrets.example.yaml secrets.yaml` and fill it.
  Both `.env` and `secrets.yaml` are gitignored.

## Demo path

Start the mock app (an intentionally hostile credit-union stand-in) in one terminal:

```sh
uv run uvicorn mockapp.app:app --host 127.0.0.1 --port 8000
```

In another terminal, load env first (`set -a; . ./.env; set +a`):

```sh
# 1. DISCOVERY (spends tokens): a real LLM drives the mock to read a member's savings balance,
#    and the run is recorded as evidence. (Uses env creds so it needs no TTY; the interactive
#    `lyrebird discover --url http://127.0.0.1:8000/login` does the same with prompts.)
uv run python scripts/discover_mock.py

# 1b. Build the Capability artifact from that discovery run (no key, no browser):
uv run python scripts/build_artifact.py

# 2. DETERMINISTIC REPLAY (no LLM) — happy path, reads the balance. Password from secrets.yaml.
uv run lyrebird replay lookup_savings_balance --url http://127.0.0.1:8000 \
    --param username="$MOCK_USERNAME" --param member_id=100001

# 3. A DIFFERENT member — same recipe, different value (proves the label-anchored locator
#    survives a per-invocation value change): 100003 -> 15320.0
uv run lyrebird replay lookup_savings_balance --url http://127.0.0.1:8000 \
    --param username="$MOCK_USERNAME" --param member_id=100003

# 4. A BUSINESS OUTCOME, not a crash — unknown member -> BUSINESS_OUTCOME / NOT_FOUND:
uv run lyrebird replay lookup_savings_balance --url http://127.0.0.1:8000 \
    --param username="$MOCK_USERNAME" --param member_id=999999

# 5. A RISKY mutating flow -> ESCALATED. Build the artifact, then replay it: the flow fills the
#    sub-account form and reaches the review page, and the risky Confirm step BLOCKS and escalates
#    to a human — it never submits (the run stops at /review, never /confirm).
uv run python scripts/build_mutating_artifact.py
uv run lyrebird replay open_subaccount --url http://127.0.0.1:8000 \
    --param username="$MOCK_USERNAME" --param member_id=100001 --param amount=250.00 \
    --pre-login --pre-nav /member/100001/subaccount --confirm-risky

# 6. OPERATOR handoff console (separate process): see the escalated run, take control of the
#    same live session, then hand it back to automation.
uv run lyrebird operator list
uv run lyrebird operator take replay-escalated --as alice
uv run lyrebird operator handback replay-escalated --note "authorized manually"
```

Each run writes a redacted evidence directory under `evidence/`. Committed examples:
`evidence/discover-127_0_0_1-*` (the real LLM discovery run), `evidence/replay-happy`
(SUCCESS on a fresh member), `evidence/replay-not-found` (BUSINESS_OUTCOME / NOT_FOUND), and
`evidence/replay-escalated` (risky Confirm blocked → PENDING_HUMAN handoff, never submitted).

## Tests

```sh
uv run pytest        # 48 tests, no browser, ~0.3s
```

Replay, policy, schema, and recorder all run **with no LLM**. A subprocess import guard
(`tests/test_no_llm_in_replay.py`) proves the replay package can't load the LLM client.

## Layout

- `mockapp/` — the intentionally hostile credit-union stand-in the agent drives.
- `src/lyrebird/` — `surface` (perceive/act seam + durable-locator resolution) · `discovery`
  (the LLM loop, the only module importing the LLM client) · `recorder` · `capability`
  (artifact schema) · `replay` (deterministic executor) · `policy` · `handoff` · `evidence`.
- `artifacts/` — the committed Capability + exported JSON Schema.
- `evidence/` — committed example runs (one discovery + two replays).
- `HOW_IT_WORKS.md` — a deeper walk-through of the mechanics.
- `REPORT.md` — the design write-up (the seven required headings).
