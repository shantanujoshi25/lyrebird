"""Shared fixtures. The `live_server` fixture boots the mock app on a real port so
Playwright (C2+) can drive it over HTTP — TestClient can't be driven by a browser.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Iterator

import pytest
import uvicorn


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _Server(uvicorn.Server):
    def install_signal_handlers(self) -> None:  # don't hijack signals in a thread
        pass


@pytest.fixture(scope="session")
def live_server() -> Iterator[str]:
    """Run mockapp.app on a background thread; yield its base URL."""
    port = _free_port()
    config = uvicorn.Config("mockapp.app:app", host="127.0.0.1", port=port, log_level="warning")
    server = _Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    # wait until it accepts connections
    for _ in range(100):
        if server.started:
            with socket.socket() as s:
                if s.connect_ex(("127.0.0.1", port)) == 0:
                    break
        time.sleep(0.05)
    else:  # pragma: no cover
        raise RuntimeError("mock server did not start")

    yield base

    server.should_exit = True
    thread.join(timeout=5)
