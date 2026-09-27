# lyrebird — Design Report

A computer-use automation system for legacy back-office apps that have no API. You give a goal
in plain English; an **LLM discovers** the UI flow once, and the successful run is frozen into a
typed, versioned **Capability** artifact. From then on that artifact **replays deterministically,
with no LLM in the decision loop** — and escalates to a **human** when it can't safely proceed.

The whole system is that one arc:

> **goal → (LLM) discover → Capability artifact → (no LLM) replay → result**

Discovery is slow, costly, and non-deterministic, so we pay for it once; replay is cheap and
identical every time, so it's what a production agent actually calls. Everything else — safety,
evidence, human handoff — wraps those two halves. Built and tested: **48 tests**, run in ~0.3s
with no browser and no API key; one real `claude-sonnet-4-6` discovery run is committed under
`evidence/`.

## 1. Architecture

Single process, Python 3.12, three CLI verbs — `discover` (the LLM run), `replay` (the
deterministic run), and `operator` (the human handoff console). Dependencies point strictly
downward: `cli → {discovery, replay, handoff} → {surface, recorder, capability, policy,
evidence}`, with `capability/` and `policy/` as pure leaves.

```text
surface/     perceive/act seam: Surface protocol + PlaywrightWebSurface + Desktop/LegacyWeb stubs
             — also owns durable-locator RESOLUTION (the only module that knows "DOM")
discovery/   the LLM observe→decide→policy→act→read loop  (the ONLY module importing the LLM client)
recorder/    trajectory → Capability (durable locators, param binding, provenance)
capability/  the Pydantic artifact schema, JSON Schema export, versioned JSON store
replay/      deterministic executor + condition detector + ReplayResult (NO locator grammar of its own)
policy/      allowlist, risk classifier, redaction (pure functions, called by both loops)
handoff/     SessionController state machine, run-state file, intervention, operator CLI
evidence/    redacted run dirs: steps.jsonl, screenshots, ARIA, run_state, result
mockapp/     the intentionally hostile credit-union stand-in
```

**Two load-bearing seams.** (1) `surface/` is the perceive/act seam: everything above it is
surface-agnostic and speaks only `role / name / value / locators`; only `PlaywrightWebSurface`
knows the word "DOM." (2) `capability/` is the LLM/deterministic seam: the artifact is the
contract that lets the LLM-driven half hand off to the LLM-free half.

**Key decision — trust the DOM, and split the job by phase.** During discovery the LLM works
from a screenshot with numbered elements (Set-of-Marks) and points at one by index — the honest
answer to "no clean DOM." But the moment it points, we stop guessing: we read that node's own
**durable handles** straight off the page (its id, `name`, a stable `data-*`, its ARIA role +
name, its exact text, and — for a value cell — the label next to it). Replay re-finds the node
with those handles via real Playwright locators.

This leans on a property the brief calls out: back-office UIs have *stable DOMs*. So a handle
taken from the node itself is a reliable locator — more so than screen coordinates (which move
when anything re-renders) or text-reconstruction heuristics (which are really *our* code guessing
what the page means).

**The LLM is the brain; our code is mechanism.** The surface reports every handle a node *has*,
without deciding. At `read_value` the LLM makes the judgment only it can: is this output value
**variable** (changes with the inputs — a balance) or **fixed**, and if variable, which stable
**label** anchors it. That judgment is recorded; replay just resolves the chosen handles. No
data-type menu, no shape grammar, no extraction sandbox — those were earlier designs we cut when
they kept encoding *our* guesses about the app instead of letting the model decide.

## 2. Artifact schema

The deepest investment (`capability/schema.py`, Pydantic v2; the JSON Schema is exported to
`artifacts/capability.schema.json` for the calling agent). A `Capability` is:

- **identity & lifecycle** — `capability_id`, `version`, `schema_version`, `status`
  (`draft`|`approved`), and the natural-language `goal` it came from;
- **the contract** — a `target` (app / vendor / entry-pattern / tenant), typed `inputs` (each with
  a `sensitive` flag) and typed `outputs`;
- **the flow** — ordered `steps`, each with a locator `target`, a verified `postcondition`, a
  `risk` flag, and `provenance` (`model` or `human`);
- **runtime knowledge** — `known_conditions` (the taxonomy below), a `success_checkpoint`, a
  `risk_summary`, and `provenance` linking to the discovery run.

Four decisions shaped it:

- **One durable locator, used for both step targets and output values.** A `DurableLocator` is a
  stable `semantic_id` plus an ordered list of `candidates`, most-stable first. Each candidate is a
  `kind` (`css` | `role` | `text` | `label` | `nth`) and a `value` (plus an accessible `name` for
  `role`, and an optional `frame`). The order matters: replay tries them top-down, and *which one
  wins* is the drift signal (winning index = "fallback depth"). Note `css` here is a concrete
  handle the node already has — `#id`, `[name=…]`, `[data-testid=…]` — never a hand-written class
  chain, and there is no XPath. Every kind also has an OS-accessibility equivalent, so the artifact
  is not web-only.
- **A value cell is anchored on its LABEL, by the LLM's judgment.** A balance's own text *is* the
  value and changes every run, so it's useless as a locator. For a variable output the recorder
  puts a `label` candidate (`"the cell next to 'Savings'"`) first, per the LLM's `value_stability`
  + `anchor_label` decision at discovery. This is the fix that makes one recipe read 100001's
  `$4,210.75` and 100003's `$15,320.00` without change. `value_seen` records what the LLM saw as a
  ground-truth sanity check.
- **The error taxonomy is a first-class type.** `OutcomeClass` (business/recoverable/hard) and
  `OutcomeCode` are enums, so the business-outcome-vs-crash distinction is type-enforced, not
  stringly-typed. `ReplayResult` is the machine-readable contract the calling agent consumes.
- **Decoupled from the transcript, leak-proof by construction.** `provenance` links to the
  discovery run id but never embeds the transcript. Typed values are bound as `{{param}}` in
  steps — the literal is never stored — and a validator forbids a `sensitive` input from carrying
  an `example` (sensitive values come from a per-run secrets file, never the artifact).

## 3. Determinism & error handling

**The determinism invariant, enforced structurally:** the `replay` package has **no import path
to the LLM client** — a subprocess test imports it and asserts the Anthropic SDK is absent from
`sys.modules`. Replay makes zero action-deciding LLM calls, ever. The only production LLM use is
an optional *diagnostic text for a human* on a genuine deviation (Section 5), which is logged and shown,
never fed to an action.

- **Locator resolution lives in the Surface.** Replay hands a `DurableLocator` to the surface,
  which tries each candidate as a real Playwright locator and takes the first that resolves to
  **exactly one** element; the winning index is the *fallback depth* (drift telemetry). A value
  in a per-run iframe is matched by a stable URL substring the LLM supplied (`frame_anchor`), so
  `.../workspace/member/100003` resolves via `workspace/member`. Nothing resolves →
  `LOCATOR_NOT_FOUND` (hard). Reading an output is the same resolution, then `.inner_text()` /
  `.input_value()`, cast to the output's **declared type** — the only per-type logic in replay.
- **Agent supervision.** Each recorded step carries a **verified postcondition** (captured at
  discovery by re-observing after the action). Replay re-checks it deterministically. If it holds,
  continue. If it fails, consult the taxonomy: a *known condition* is handled; nothing explaining
  it is a **deviation** → human handoff.
- **Result contract** (`ReplayResult`) distinguishes `SUCCESS` / `BUSINESS_OUTCOME` / `FAILURE` /
  `ESCALATED` with `outcome_code`, `failed_step`, `expected`, `observed`, `fallback_depths`. A
  handled recoverable condition does **not** turn a success into a failure. Waiting is
  condition-based, not sleeps.

Demonstrated end-to-end against the live mock, each with the exact status: happy path
(`SUCCESS`, `4210.75`); a **fresh member** (`SUCCESS`, `15320.0` — the value-varies proof); and an
unknown member (`BUSINESS_OUTCOME` / `NOT_FOUND`, detected by the recorded "No such member"
condition, never a crash). The committed `evidence/replay-*` runs are exactly these.

## 4. Heterogeneity & multi-tenant

Implemented against one surface; designed so the core doesn't paint us into a corner. The big
generalization win: **no per-site code.** A natural-language goal + URL is enough — the agent
infers the contract, and locators come from the node itself rather than a per-app rule.

- **Surface extension.** The `Surface` protocol (`observe` / `act` / `page_text` /
  `resolve_locator` / `read_locator` / `act_locator`) is browser-free.
  `PlaywrightWebSurface` flattens frames/iframes into one element list with a `frame:` container
  path — exactly how a desktop window hierarchy would flatten. `DesktopSurface` and
  `LegacyWebSurface` are stubs that **satisfy the same protocol** (an `isinstance` test proves it),
  so the seam is demonstrably satisfiable without a browser. The durable-locator kinds were chosen
  so each has a desktop-AX equivalent (role+name, exact text, ordinal), which is why no
  web-only selector kind exists.
- **Cross-tenant reuse.** Steps and outputs reference elements by `semantic_id`; the `target`
  carries `app_id` / `vendor` / `tenant`. A second tenant running the same vendor product is "add a
  per-tenant override candidate list keyed on `semantic_id`," not "re-record." Drift is measurable
  *today* via fallback depth (a step that used to resolve at candidate 0 now resolving deeper). We
  compute the telemetry; we don't build the tenant-override table (cut, below).

## 5. Escalation & handoff

The control-transfer model is real (a full co-browsing console is out of scope; the mechanism is
not). The channel is a **run-state file the engine polls**, not an in-memory lock — because the
operator runs in a separate process, so "who is in control" is always answerable by reading
`run_state.json`.

`SessionController` enforces `AUTOMATION → PENDING_HUMAN → HUMAN → AUTOMATION → DONE` with a
legal-transition guard. On a stuck trigger the engine writes an `intervention_request.json`
(capability, step, url, reason, screenshot, digest), flips to `PENDING_HUMAN`, and polls until an
operator hands control back — then **re-verifies a checkpoint before resuming**. The operator CLI
(`lyrebird operator list / take / handback`) is a separate process writing the same files.

**Demonstrated end-to-end.** The committed `evidence/replay-escalated` run is real: replaying the
mutating `open_subaccount` capability fills the sub-account form, reaches the review page, and the
risky **Confirm** step blocks and escalates — the run stops at `/review`, **never reaches
`/confirm`** (the mock has no submit handler; reaching it would 404, which we never do). The
run-state is `PENDING_HUMAN` with a full intervention request; `lyrebird operator take` /
`handback` then transfers control of that same run through `HUMAN → AUTOMATION`. Tested by
`test_handoff.py` (the state-machine round-trip) and `test_committed_evidence.py` (the committed
escalated run is a real handoff that never submitted).

**One mechanism, two roles.** In **replay**, on a risky/irreversible step or an unexplained
deviation, the human is the **decision-maker**: the LLM may write a diagnostic ("expected X,
observed Y") shown to the operator — **text only, never an action** — and the deviation is logged
for offline artifact update, never live self-modification. In **discovery**, the same machinery
lets a stuck LLM defer to a human **teacher** whose manual step is captured (element descriptors →
the same durable locator, stamped `provenance: human`) and folded into the artifact so it replays
deterministically. The risky-step gate is enforced by the executor (Section 6); the discovery-side
controller wiring is the documented cut.

## 6. Safety

Three layers, enforced before every action in both loops, all fail-closed:

- **Allowlist** (`policy.yaml`): permitted domains, URL-pattern globs, and action types. Anything
  not explicitly allowed is denied; an empty/unparseable URL is denied; a missing policy file
  raises. `navigate` is checked against the *target* URL, not the current page.
- **Risky actions.** `classify_risk` flags activating actions whose target text matches risky
  patterns (submit/confirm/transfer/delete/close). A risky step is **blocked in unattended replay
  unless the artifact is `approved` AND the caller passes `confirm_risky`**, else it escalates. The
  bias is toward over-flagging: a false "risky" escalates to a human; a false "safe" is the
  dangerous one.
- **Redaction & secrets.** Sensitive params never enter the artifact, the CLI args, or shell
  history — they live in a local, gitignored `secrets.yaml` read at run time, keyed by the
  declared sensitive input names. Evidence redacts those values on the write path; a regex scrubber
  removes SSN/long-account patterns; screenshots are masked at the sensitive-field bbox. An
  end-to-end audit over all committed evidence is clean.

**Limits.** The risk classifier is text-pattern based (deliberately conservative); the allowlist
is coarse-grained; screenshot masking depends on correct sensitive-field bboxes. Adequate for the
slice, and documented seams.

## 7. Cuts

Deliberate, documented, all stubbed at real seams:

- **The mutating `open_subaccount` artifact is authored, not LLM-discovered.** It exists to
  exercise the risky-step gate + handoff (Section 5), which needs a risky flow, not another discovery
  proof — the lookup capability already covers real discovery. Its step locators were read off the
  live mock DOM so they resolve deterministically; a full discovery run of it is a token cost with
  little marginal signal. Next (cheap): record it via the LLM too, for symmetry.
- **Second tenant / override layer** — *designed* (semantic-id indirection, drift telemetry
  computed today), not *built*. Next: a `semantic_id`-keyed override table + a test replaying the
  base artifact against a second variant.
- **Desktop / legacy-web surfaces** — real protocol-conforming stubs that type-check. Next:
  implement `DesktopSurface` over an OS accessibility API.
- **Discovery→controller wiring** — the `SessionController` is proven end-to-end via *replay*
  escalation; the discovery loop escalates on dead-end/policy-block. Full discovery-side wiring to
  the controller is a small step; the mechanism is unified.
- **Offline artifact-update pipeline** — production deviations are *logged* with the LLM's
  diagnostic and the seam is designed; the review-and-regenerate workflow is not built (it would
  balloon past the time-box). We deliberately never self-modify a deterministic capability live.
- **Assisted fallback, confidence scoring, code-gen, multi-run stability** — out of scope; the
  fallback-depth telemetry is the hook a confidence score would build on.

What I'd build next, in order: the second-tenant override layer (discharges the multi-tenant
story with running code), then the offline artifact-update pipeline (consume the deviation logs),
then a confidence score gating draft→approved.
