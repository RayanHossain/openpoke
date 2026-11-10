"""Pytest wiring for the OpenPoke routing baseline.

Importing ``harness`` first is what guarantees the sandboxed import of
``server`` (see harness.py docstring); keep it the first import.
"""

from __future__ import annotations

import asyncio

import pytest

import harness  # noqa: F401  (side effect: sandboxed import of `server`)
from routing_summary import pytest_configure  # noqa: F401  (markers + end-of-run summary)


@pytest.fixture
def sb(tmp_path):
    sandbox = harness.open_sandbox(tmp_path / "data")
    try:
        yield sandbox
    finally:
        sandbox.close()


@pytest.fixture
def run():
    """Run a coroutine factory on a fresh event loop, then drain background tasks."""

    def _run(coro_fn, *, drain=True):
        async def _main():
            result = await coro_fn()
            if drain:
                await harness.drain()
            return result

        return asyncio.run(_main())

    return _run
