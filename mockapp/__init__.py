"""mockapp — a self-built, intentionally hostile credit-union back-office stand-in.

Server-rendered (FastAPI + Jinja2), no clean DOM: iframe workspace, table layout, no
`id`/`data-testid`, inline handlers, generic class names, labels by cell adjacency.
It is the target surface for discovery and replay. See docs/01_ARCHITECTURE.md §8.

No real credentials, no real PII. Member fixtures are obviously fake.
"""
