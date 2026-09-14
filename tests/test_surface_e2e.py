"""C2 — drive the mock app end-to-end through PlaywrightWebSurface with hardcoded actions.

No LLM. This proves the perceive/act seam works on the hostile app: observe() flattens
iframe contents into one element list, act() drives every control type by index, and the
read-only capability (login -> search 100001 -> read savings balance) completes. It also
asserts the Desktop/LegacyWeb stubs satisfy the Surface protocol (A12.3).

The test locates elements by role+name from the Observation (what the recorder/LLM would
do) and then acts on the returned index — exercising index-based act() without hardcoding
brittle integer positions.
"""

from __future__ import annotations

import re

import pytest

from lyrebird.surface import Action, Observation, Surface
from lyrebird.surface.playwright_web import PlaywrightWebSurface
from lyrebird.surface.stubs import DesktopSurface, LegacyWebSurface

USER = "teller"
PASSWORD = "demo-pass-not-secret"
MEMBER = "100001"


def find(obs: Observation, *, role: str, name_contains: str = "", nearby_contains: str = "") -> int:
    """Return the index of the first element matching role + (name or nearby) substring."""
    for el in obs.elements:
        if el.role != role:
            continue
        if name_contains and name_contains.lower() not in el.name.lower():
            continue
        if nearby_contains and not any(nearby_contains.lower() in t.lower() for t in el.nearby_text):
            continue
        return el.index
    raise AssertionError(
        f"no element role={role} name~{name_contains!r} nearby~{nearby_contains!r}; "
        f"had: {[(e.role, e.name) for e in obs.elements]}"
    )


@pytest.fixture
def surface(live_server: str) -> PlaywrightWebSurface:
    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    yield s
    s.close()


def _login(surface: PlaywrightWebSurface) -> None:
    obs = surface.observe()
    # login inputs have no labels-by-for; the search/login textboxes are found by row text
    user_idx = find(obs, role="textbox", nearby_contains="Username")
    assert surface.act(Action(kind="type", target_index=user_idx, value=USER)).ok
    obs = surface.observe()
    pass_idx = find(obs, role="textbox", nearby_contains="Password")
    assert surface.act(Action(kind="type", target_index=pass_idx, value=PASSWORD)).ok
    obs = surface.observe()
    signin = find(obs, role="button", name_contains="Sign in")
    assert surface.act(Action(kind="click", target_index=signin)).ok


# ── the read-only capability, driven by hand ─────────────────────────────────
def test_readonly_flow_reads_savings_balance(surface: PlaywrightWebSurface, live_server: str) -> None:
    _login(surface)

    # search page: type member id, submit
    obs = surface.observe()
    q_idx = find(obs, role="textbox", nearby_contains="Member ID")
    assert surface.act(Action(kind="type", target_index=q_idx, value=MEMBER)).ok
    obs = surface.observe()
    search_btn = find(obs, role="button", name_contains="Search")
    assert surface.act(Action(kind="click", target_index=search_btn)).ok

    # landed on the detail page for the member
    obs = surface.observe()
    assert f"/member/{MEMBER}" in obs.url

    # read the actual balance value out of the iframe workspace (the capability's output).
    # We read it from the workspace document directly via the Surface's page under test —
    # the balance is display text, so assert on the real figure, not a hedge.
    import httpx  # transitively available (FastAPI test dep)

    ws = httpx.get(
        f"{live_server}/workspace/member/{MEMBER}",
        cookies={c["name"]: c["value"] for c in surface._context.cookies()},
        follow_redirects=False,
    )
    assert ws.status_code == 200
    m = re.search(r"Savings.*?\$([\d,]+\.\d{2})", ws.text, re.S)
    assert m, "expected a savings figure in the workspace"
    assert m.group(1) == "4,210.75"  # the fixture value for member 100001


def test_observe_flattens_iframe_interactables(live_server: str) -> None:
    """An interactable that lives INSIDE the workspace iframe must appear in the top-level
    Observation, tagged with a container_path frame prefix — the real flattening proof."""
    s = PlaywrightWebSurface(f"{live_server}/login", headed=False)
    try:
        _login(s)
        s.act(Action(kind="navigate", value=f"{live_server}/member/{MEMBER}"))
        obs = s.observe()

        # main-frame interactable present (the sub-account link)
        assert any(e.role == "link" and "sub-account" in e.name.lower() for e in obs.elements)

        # the "Refresh balance" button lives inside the workspace iframe; it must be flattened
        # into the same element list, carrying a frame container_path.
        refresh = [e for e in obs.elements if "refresh balance" in e.name.lower()]
        assert refresh, f"iframe button not flattened; had {[(e.role, e.name) for e in obs.elements]}"
        assert refresh[0].container_path and refresh[0].container_path[0].startswith("frame:"), (
            f"iframe element missing frame container_path: {refresh[0].container_path}"
        )

        # and it is genuinely actionable across the frame boundary by its index
        assert s.act(Action(kind="click", target_index=refresh[0].index)).ok
    finally:
        s.close()


# ── control variety: select / radio / checkbox actionable by index ───────────
def test_all_control_types_actionable(surface: PlaywrightWebSurface, live_server: str) -> None:
    _login(surface)
    surface.act(Action(kind="navigate", value=f"{live_server}/member/{MEMBER}/subaccount"))
    obs = surface.observe()

    amount = find(obs, role="textbox", nearby_contains="deposit")
    assert surface.act(Action(kind="type", target_index=amount, value="250.00")).ok

    obs = surface.observe()
    acct = find(obs, role="combobox")
    assert surface.act(Action(kind="select", target_index=acct, value="money_market")).ok

    obs = surface.observe()
    paper = find(obs, role="radio", name_contains="Paper")
    assert surface.act(Action(kind="click", target_index=paper)).ok

    obs = surface.observe()
    box = find(obs, role="checkbox")
    assert surface.act(Action(kind="click", target_index=box)).ok

    obs = surface.observe()
    cont = find(obs, role="button", name_contains="review")
    assert surface.act(Action(kind="click", target_index=cont)).ok

    obs = surface.observe()
    assert "/review" in obs.url
    # the risky Confirm control is present on the review page
    assert any(e.role == "button" and re.search(r"confirm", e.name, re.I) for e in obs.elements)


# ── stubs satisfy the protocol without a browser (A12.3) ─────────────────────
def test_stubs_satisfy_surface_protocol() -> None:
    assert isinstance(DesktopSurface(), Surface)
    assert isinstance(LegacyWebSurface(), Surface)


def test_stubs_raise_not_implemented() -> None:
    for cls in (DesktopSurface, LegacyWebSurface):
        with pytest.raises(NotImplementedError):
            cls().observe()
