"""Hostile HTML templates for the mock app (C1).

Every template here obeys the "no clean DOM" invariants (asserted by tests):
  * no `id=` and no `data-testid=` attributes anywhere,
  * table-based layout (no semantic landmarks),
  * inline event handlers (onclick/onsubmit/onchange),
  * generic class names (c1/col/x/...),
  * form-control labels associated with controls ONLY by table-cell adjacency
    (no <label for=>, no wrapping <label>) — this is what forces the
    label-proximity / relative-anchor locator strategies in replay.

Kept as Jinja string templates so the exact markup is auditable in one file. Autoescape is
ON, so member-supplied values render safely; the structural hostility is all literal.
"""

from __future__ import annotations

from jinja2 import Template

# A shared minimal page shell. No <title id>, no landmarks, generic body class.
_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title></head>
<body class="app">{body}</body></html>"""


def _page(title: str, body: str) -> str:
    return _PAGE.format(title=title, body=body)


# ── login ─────────────────────────────────────────────────────────────────────
LOGIN = Template(
    _page(
        "Teller Console",
        """
    <table class="c1"><tr><td class="col">
      <h2>Teller Console</h2>
      {% if error %}<p class="x">{{ error }}</p>{% endif %}
      <form method="post" action="/login" onsubmit="return true">
        <table class="c1">
          <tr><td class="col">Username</td><td class="col">
            <input type="text" name="u" value=""></td></tr>
          <tr><td class="col">Password</td><td class="col">
            <input type="password" name="p" value=""></td></tr>
          <tr><td class="col"></td><td class="col">
            <button type="submit" onclick="return true">Sign in</button></td></tr>
        </table>
      </form>
    </td></tr></table>""",
    )
)

# ── member search ──────────────────────────────────────────────────────────
SEARCH = Template(
    _page(
        "Member Search",
        """
    <table class="c1"><tr><td class="col">
      <h2>Member Search</h2>
      <form method="get" action="/member/lookup" onsubmit="return true">
        <table class="c1">
          <tr><td class="col">Member ID</td><td class="col">
            <input type="text" name="q" value=""></td>
          <td class="col"><button type="submit" onclick="return true">Search</button></td></tr>
        </table>
      </form>
    </td></tr></table>""",
    )
)

# ── member detail (holds the iframe workspace) ───────────────────────────────
DETAIL = Template(
    _page(
        "Member Detail",
        """
    {% if interstitial %}
    <table class="modal"><tr><td class="col">
      <h3>System notice</h3>
      <p class="x">Scheduled maintenance window tonight.</p>
      <button type="button" onclick="this.closest('table').style.display='none'">Dismiss</button>
    </td></tr></table>
    {% endif %}
    <table class="c1"><tr><td class="col">
      <h2>Member {{ member_id }}</h2>
      <table class="c1"><tr><td class="col">
        <iframe src="/workspace/member/{{ member_id }}" class="ws" width="600" height="240"></iframe>
      </td></tr></table>
      <p class="x"><a href="/member/{{ member_id }}/subaccount">Open sub-account</a></p>
    </td></tr></table>""",
    )
)

DETAIL_NOT_FOUND = Template(
    _page(
        "Member Detail",
        """
    <table class="c1"><tr><td class="col">
      <h2>Member {{ member_id }}</h2>
      <p class="x">No such member.</p>
    </td></tr></table>""",
    )
)

DETAIL_DENIED = Template(
    _page(
        "Member Detail",
        """
    <table class="c1"><tr><td class="col">
      <h2>Member {{ member_id }}</h2>
      <p class="x">Not authorized to view this member.</p>
    </td></tr></table>""",
    )
)

# ── workspace (inside the iframe): balance in a nested table cell ────────────
WORKSPACE = Template(
    _page(
        "Workspace",
        """
    <table class="c1"><tr><td class="col">
      <h3>{{ name }}</h3>
      <table class="c1">
        <tr><td class="col">Checking</td><td class="col">$0.00</td></tr>
        <tr><td class="col">Savings</td><td class="col">${{ savings }}</td></tr>
      </table>
      <button type="button" onclick="location.reload()">Refresh balance</button>
    </td></tr></table>""",
    )
)

WORKSPACE_NOT_FOUND = Template(
    _page("Workspace", """<table class="c1"><tr><td class="col"><p class="x">No such member.</p></td></tr></table>""")
)

# ── subaccount form: text + <select> + radio group + checkbox ────────────────
# Labels sit in the LEFT cell; controls in the RIGHT cell of the same row. There is no
# <label for=> anywhere — association is by table-cell adjacency only, on purpose.
SUBACCOUNT_FORM = Template(
    _page(
        "Open Sub-account",
        """
    <table class="c1"><tr><td class="col">
      <h2>Open Sub-account for {{ member_id }}</h2>
      <form method="post" action="/member/{{ member_id }}/subaccount/review" onsubmit="return true">
        <table class="c1">
          <tr>
            <td class="col">Initial deposit</td>
            <td class="col"><input type="text" name="amount" value=""></td>
          </tr>
          <tr>
            <td class="col">Account type</td>
            <td class="col">
              <select name="acct_type" onchange="return true">
                <option value="savings">Savings</option>
                <option value="checking">Checking</option>
                <option value="money_market">Money Market</option>
              </select>
            </td>
          </tr>
          <tr>
            <td class="col">Statement delivery</td>
            <td class="col">
              <input type="radio" name="statement" value="paper"> Paper
              <input type="radio" name="statement" value="electronic"> Electronic
            </td>
          </tr>
          <tr>
            <td class="col">Authorization</td>
            <td class="col">
              <input type="checkbox" name="authorized" value="on"> Member authorized this action
            </td>
          </tr>
          <tr><td class="col"></td><td class="col">
            <button type="submit" onclick="return true">Continue to review</button></td></tr>
        </table>
      </form>
    </td></tr></table>""",
    )
)

# ── review: echoes chosen values; Confirm is the risky control ───────────────
REVIEW = Template(
    _page(
        "Review Sub-account",
        """
    <table class="c1"><tr><td class="col">
      <h2>Review</h2>
      <table class="c1">
        <tr><td class="col">Member</td><td class="col">{{ member_id }}</td></tr>
        <tr><td class="col">Initial deposit</td><td class="col">{{ amount }}</td></tr>
        <tr><td class="col">Account type</td><td class="col">{{ acct_type_label }}</td></tr>
        <tr><td class="col">Statement delivery</td><td class="col">{{ statement_label }}</td></tr>
      </table>
      <form method="post" action="/member/{{ member_id }}/subaccount/confirm" onsubmit="return true">
        <button type="submit" onclick="return true">Confirm and open account</button>
      </form>
    </td></tr></table>""",
    )
)

REVIEW_INVALID = Template(
    _page(
        "Review Sub-account",
        """
    <table class="c1"><tr><td class="col">
      <h2>Review</h2>
      <p class="x">{{ message }}</p>
    </td></tr></table>""",
    )
)
