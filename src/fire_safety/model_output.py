"""Run-scoped capture of the model's verbatim response text.

The result page shows the raw completion unchanged, so a reader can tell a
model that answered wrongly from one that answered faithfully and was rejected
by the schema. Capture rides on a :class:`~contextvars.ContextVar` for the same
reason :mod:`fire_safety.timing` does: the pipeline and the Qwen client return
exactly what they always returned, and capturing is a no-op for every caller
that never opened a :func:`capturing` block.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_captured_text: ContextVar[str | None] = ContextVar("fire_safety_model_output", default=None)


def capture_model_output(text: str) -> None:
    """Keep ``text`` for the run in this context; the last call wins."""

    _captured_text.set(text)


def captured_model_output() -> str | None:
    """Return the text captured for this run, or ``None`` when there was none."""

    return _captured_text.get()


@contextmanager
def capturing() -> Iterator[None]:
    """Start a fresh capture for the duration of the ``with`` block.

    A run that produces no output must not read the previous run's text, so the
    slot starts empty and is restored on exit.
    """

    token = _captured_text.set(None)
    try:
        yield
    finally:
        _captured_text.reset(token)


__all__ = ["capture_model_output", "captured_model_output", "capturing"]
