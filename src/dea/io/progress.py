"""Progress of a long-running import, for whoever is waiting on it.

The import code calls :func:`report` at its stages ("reading icu/chartevents", "running the
checks"). By default that goes nowhere; a caller that wants to show progress wraps the work
in :func:`track` with a callback, which the API uses to stream the stage of a background job
to the browser. The hook is a context variable, so parallel imports do not mix.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from contextvars import ContextVar

Callback = Callable[[str, str], None]
_hook: ContextVar[Callback | None] = ContextVar("dea_progress", default=None)


def report(stage: str, detail: str = "") -> None:
    """Announce a stage; a no-op unless someone is tracking."""
    hook = _hook.get()
    if hook is not None:
        hook(stage, detail)


@contextlib.contextmanager
def track(callback: Callback) -> Iterator[None]:
    """Route :func:`report` calls inside the block to ``callback(stage, detail)``."""
    token = _hook.set(callback)
    try:
        yield
    finally:
        _hook.reset(token)


__all__ = ["report", "track"]
