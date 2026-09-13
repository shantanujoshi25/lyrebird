"""C1 — HTTP-level tests for the hostile mock app. No browser, no LLM.

These define the mock app's contract: every page and every injectable condition, plus the
control-variety and hostility invariants from docs/01_ARCHITECTURE.md §8. If a test here
regresses, the "no clean DOM" property or the taxonomy mapping has silently broken.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from mockapp.app import CREDS, app

MEMBER_OK = "100001"          # exists, authorized
MEMBER_MISSING = "999999"     # does not exist -> NOT_FOUND
MEMBER_FORBIDDEN = "100002"   # exists but permission-denied when injected


@pytest.fixture
def client() -> TestClient:
    # follow_redirects=False so we can assert on the 303 -> /login redirects (session expiry, auth).
    return TestClient(app, follow_redirects=False)


def login(client: TestClient) -> None:
    """Log in with the good creds and keep the session cookie on the client."""
    resp = client.post("/login", data={"u": CREDS["username"], "p": CREDS["password"]})
    assert resp.status_code == 303, resp.text
    assert resp.headers["location"] == "/member"


# ── auth ────────────────────────────────────────────────────────────────────
def test_login_page_renders(client: TestClient) -> None:
    resp = client.get("/login")
    assert resp.status_code == 200
    assert "Teller Console" in resp.text


def test_login_rejects_bad_credentials(client: TestClient) -> None:
    resp = client.post("/login", data={"u": "nope", "p": "wrong"})
    assert resp.status_code == 200          # re-renders the form, does not redirect
    assert "Invalid credentials" in resp.text


def test_login_accepts_good_credentials(client: TestClient) -> None:
    resp = client.post("/login", data={"u": CREDS["username"], "p": CREDS["password"]})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/member"


def test_member_search_requires_login(client: TestClient) -> None:
    resp = client.get("/member")
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


# ── read-only flow: search -> detail -> balance ──────────────────────────────
def test_member_search_page(client: TestClient) -> None:
    login(client)
    resp = client.get("/member")
    assert resp.status_code == 200
    assert "Member Search" in resp.text


def test_search_lookup_redirects_to_detail(client: TestClient) -> None:
    # The search form is a real GET (works without JS) that redirects to the detail page,
    # so the Surface can drive it by submitting the form.
    login(client)
    resp = client.get(f"/member/lookup?q={MEMBER_OK}")
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/member/{MEMBER_OK}"


def test_member_detail_has_iframe_workspace(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/member/{MEMBER_OK}")
    assert resp.status_code == 200
    # The detail is an iframe workspace (the frame-flattening test target for C2).
    assert "<iframe" in resp.text
    assert f"/workspace/member/{MEMBER_OK}" in resp.text


def test_workspace_shows_savings_balance(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/workspace/member/{MEMBER_OK}")
    assert resp.status_code == 200
    # Balance lives in a nested table cell; the read-only capability extracts it.
    assert "Savings" in resp.text
    assert re.search(r"\$[\d,]+\.\d{2}", resp.text), "expected a formatted balance"


# ── injectable conditions (business outcomes) ─────────────────────────────────
def test_inject_not_found(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/member/{MEMBER_MISSING}")
    assert resp.status_code == 200
    assert "No such member" in resp.text


def test_inject_permission_denied(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/member/{MEMBER_FORBIDDEN}?inject=permission_denied")
    assert resp.status_code == 200
    assert "Not authorized" in resp.text


def test_inject_validation_error_on_subaccount(client: TestClient) -> None:
    login(client)
    resp = client.post(
        f"/member/{MEMBER_OK}/subaccount/review?inject=validation_error",
        data={"amount": "100", "acct_type": "savings", "statement": "electronic", "authorized": "on"},
    )
    assert resp.status_code == 200
    assert "Validation error" in resp.text


# ── injectable conditions (recoverable) ───────────────────────────────────────
def test_inject_interstitial_modal(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/member/{MEMBER_OK}?inject=interstitial")
    assert resp.status_code == 200
    assert "System notice" in resp.text          # a dismissible modal overlay


def test_inject_slow_load_still_renders(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/member/{MEMBER_OK}?inject=slow_load")
    assert resp.status_code == 200                # delayed, but 200 — replay waits, not sleeps
    assert MEMBER_OK in resp.text


def test_inject_http_500(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/member/{MEMBER_OK}?inject=http_500")
    assert resp.status_code == 500


def test_inject_session_expiry_redirects_after_n(client: TestClient) -> None:
    login(client)
    # After SESSION_EXPIRY_AFTER requests under this inject, the session is invalidated.
    last = None
    for _ in range(5):
        last = client.get(f"/member/{MEMBER_OK}?inject=session_expiry_after_n")
    assert last is not None
    assert last.status_code == 303
    assert last.headers["location"] == "/login"


# ── mutating flow: control variety + review echo ──────────────────────────────
def test_subaccount_form_has_all_control_types(client: TestClient) -> None:
    login(client)
    resp = client.get(f"/member/{MEMBER_OK}/subaccount")
    assert resp.status_code == 200
    html = resp.text
    assert "<select" in html, "expected a dropdown (account type)"
    assert 'type="radio"' in html, "expected a radio group (statement delivery)"
    assert 'type="checkbox"' in html, "expected a checkbox (authorization)"
    assert 'type="text"' in html or 'name="amount"' in html, "expected a text input (amount)"


def test_review_echoes_chosen_values(client: TestClient) -> None:
    login(client)
    resp = client.post(
        f"/member/{MEMBER_OK}/subaccount/review",
        data={"amount": "250.00", "acct_type": "money_market", "statement": "paper", "authorized": "on"},
    )
    assert resp.status_code == 200
    html = resp.text
    assert "Review" in html
    assert "250.00" in html
    assert "Money Market" in html      # dropdown choice echoed with a human label
    assert "Paper" in html             # radio choice echoed
    # The risky Confirm control is present on the review page (blocked in unattended replay, C6b).
    assert re.search(r"confirm", html, re.IGNORECASE)


def test_unchecked_authorization_is_validation_error(client: TestClient) -> None:
    login(client)
    # Omitting `authorized` (unchecked box) is an input-driven business outcome,
    # distinct from the injected validation_error.
    resp = client.post(
        f"/member/{MEMBER_OK}/subaccount/review",
        data={"amount": "250.00", "acct_type": "savings", "statement": "electronic"},
    )
    assert resp.status_code == 200
    assert "Validation error" in resp.text
    assert re.search(r"authoriz", resp.text, re.IGNORECASE)


# ── hostility invariants (the "no clean DOM" guard) ───────────────────────────
@pytest.mark.parametrize(
    "path, needs_login",
    [
        ("/login", False),
        ("/member", True),
        (f"/member/{MEMBER_OK}", True),
        (f"/workspace/member/{MEMBER_OK}", True),
        (f"/member/{MEMBER_OK}/subaccount", True),
    ],
)
def test_no_ids_or_testids_anywhere(client: TestClient, path: str, needs_login: bool) -> None:
    if needs_login:
        login(client)
    html = client.get(path).text
    # The whole premise: legacy back-office apps have no stable hooks. If an `id=` or
    # `data-testid=` ever appears, the "no clean DOM" property has regressed and the
    # locator strategies would be getting an unfair advantage.
    assert not re.search(r"\bid\s*=", html), f"{path} leaked an id attribute"
    assert "data-testid" not in html, f"{path} leaked a data-testid"


def test_layout_is_table_based_with_inline_handlers(client: TestClient) -> None:
    login(client)
    html = client.get(f"/member/{MEMBER_OK}/subaccount").text
    assert "<table" in html, "expected table-based layout"
    assert re.search(r"on(click|submit|change)\s*=", html), "expected inline event handlers"
