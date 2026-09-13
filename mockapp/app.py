"""The hostile mock app (C1).

FastAPI + server-rendered HTML. Deliberately legacy: no `id`/`data-testid`, table-based
layout, iframe workspace, inline event handlers, generic class names, labels associated
with controls only by table-cell adjacency. This is the "no clean DOM" surface that the
locator strategies (role+name / visible-text / label-proximity / relative-anchor) must
cope with — see docs/01_ARCHITECTURE.md §8.

HTML is assembled from Jinja string templates in `mockapp/templates.py` so the exact
hostile markup is easy to read and audit in one place. No real creds, no real PII.
"""

from __future__ import annotations

import os
import time

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from mockapp import conditions as C
from mockapp import templates as T

# ── fake creds (env-overridable; obviously not secret) ────────────────────────
CREDS = {
    "username": os.environ.get("MOCK_USERNAME", "teller"),
    "password": os.environ.get("MOCK_PASSWORD", "demo-pass-not-secret"),
}

# ── in-memory member fixtures (obviously fake) ────────────────────────────────
# member_id -> (name, savings_balance). MEMBER 100002 is used for permission-denied.
MEMBERS: dict[str, dict[str, object]] = {
    "100001": {"name": "Ada Lovelace", "savings": 4210.75},
    "100002": {"name": "Grace Hopper", "savings": 8890.10},
    "100003": {"name": "Katherine Johnson", "savings": 15320.00},
}

app = FastAPI(title="Lyrebird Mock — Teller Console")

# Session state kept server-side in a dict keyed by an opaque cookie value. This is a mock;
# a real app would use signed sessions. Value: {"user": str, "req_count": int}.
_SESSIONS: dict[str, dict[str, object]] = {}
_COOKIE = "mock_sid"


def _session(request: Request) -> dict[str, object] | None:
    sid = request.cookies.get(_COOKIE)
    if sid and sid in _SESSIONS:
        return _SESSIONS[sid]
    return None


def _require_login(request: Request) -> RedirectResponse | None:
    if _session(request) is None:
        return RedirectResponse("/login", status_code=303)
    return None


# ── auth ──────────────────────────────────────────────────────────────────────
@app.get("/login", response_class=HTMLResponse)
def login_form() -> str:
    return T.LOGIN.render(error=None)


@app.post("/login")
def login_submit(u: str = Form(...), p: str = Form(...)) -> Response:
    if u == CREDS["username"] and p == CREDS["password"]:
        sid = os.urandom(16).hex()
        _SESSIONS[sid] = {"user": u, "req_count": 0}
        resp = RedirectResponse("/member", status_code=303)
        resp.set_cookie(_COOKIE, sid, httponly=True)
        return resp
    return HTMLResponse(T.LOGIN.render(error="Invalid credentials"))


# ── read-only flow ──────────────────────────────────────────────────────────
@app.get("/member", response_class=HTMLResponse)
def member_search(request: Request) -> Response:
    if (r := _require_login(request)) is not None:
        return r
    return HTMLResponse(T.SEARCH.render())


@app.get("/member/lookup")
def member_lookup(request: Request, q: str = "") -> Response:
    """Search form target. Server-side redirect to the detail page.

    The form works without JavaScript (a real GET that redirects here), so the Surface can
    drive it by submit; the inline `onsubmit` is cosmetic hostility, not load-bearing.
    """
    if (r := _require_login(request)) is not None:
        return r
    return RedirectResponse(f"/member/{q.strip()}", status_code=303)


@app.get("/member/{member_id}", response_class=HTMLResponse)
def member_detail(request: Request, member_id: str, inject: str | None = None) -> Response:
    if (r := _require_login(request)) is not None:
        return r
    cond = C.Inject.parse(inject)

    # Session-expiry: count requests under this inject; after N, invalidate the session.
    if cond is C.Inject.SESSION_EXPIRY:
        sess = _session(request)
        assert sess is not None
        sess["req_count"] = int(sess["req_count"]) + 1
        if int(sess["req_count"]) > C.SESSION_EXPIRY_AFTER:
            sid = request.cookies.get(_COOKIE)
            if sid:
                _SESSIONS.pop(sid, None)
            return RedirectResponse("/login", status_code=303)

    if cond is C.Inject.HTTP_500:
        return HTMLResponse("Internal Server Error", status_code=500)

    if cond is C.Inject.SLOW_LOAD:
        time.sleep(C.SLOW_LOAD_DELAY_S)  # replay must WAIT on a condition, not sleep blindly

    if member_id not in MEMBERS:
        return HTMLResponse(T.DETAIL_NOT_FOUND.render(member_id=member_id))

    if cond is C.Inject.PERMISSION_DENIED:
        return HTMLResponse(T.DETAIL_DENIED.render(member_id=member_id))

    interstitial = cond is C.Inject.INTERSTITIAL
    return HTMLResponse(T.DETAIL.render(member_id=member_id, interstitial=interstitial))


@app.get("/workspace/member/{member_id}", response_class=HTMLResponse)
def workspace(request: Request, member_id: str) -> Response:
    if (r := _require_login(request)) is not None:
        return r
    m = MEMBERS.get(member_id)
    if m is None:
        return HTMLResponse(T.WORKSPACE_NOT_FOUND.render(member_id=member_id))
    return HTMLResponse(
        T.WORKSPACE.render(name=m["name"], savings=f"{m['savings']:,.2f}")
    )


# ── mutating flow: subaccount form -> review (Confirm is the risky step) ──────
@app.get("/member/{member_id}/subaccount", response_class=HTMLResponse)
def subaccount_form(request: Request, member_id: str) -> Response:
    if (r := _require_login(request)) is not None:
        return r
    return HTMLResponse(T.SUBACCOUNT_FORM.render(member_id=member_id))


ACCT_TYPE_LABELS = {"savings": "Savings", "checking": "Checking", "money_market": "Money Market"}
STATEMENT_LABELS = {"paper": "Paper", "electronic": "Electronic"}


@app.post("/member/{member_id}/subaccount/review", response_class=HTMLResponse)
def subaccount_review(
    request: Request,
    member_id: str,
    inject: str | None = None,
    amount: str = Form(""),
    acct_type: str = Form(""),
    statement: str = Form(""),
    authorized: str | None = Form(None),  # checkbox: present only when checked
) -> Response:
    if (r := _require_login(request)) is not None:
        return r
    cond = C.Inject.parse(inject)

    # Two validation-error paths: the injected one, and the input-driven unchecked box.
    if cond is C.Inject.VALIDATION_ERROR:
        return HTMLResponse(T.REVIEW_INVALID.render(message="Validation error: amount exceeds limit."))
    if authorized is None:
        return HTMLResponse(
            T.REVIEW_INVALID.render(message="Validation error: member authorization is required.")
        )

    return HTMLResponse(
        T.REVIEW.render(
            member_id=member_id,
            amount=amount,
            acct_type_label=ACCT_TYPE_LABELS.get(acct_type, acct_type),
            statement_label=STATEMENT_LABELS.get(statement, statement),
        )
    )
