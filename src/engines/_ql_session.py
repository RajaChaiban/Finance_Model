"""Process-wide QuantLib session helpers.

Why this module exists
----------------------
``ql.Settings.instance().evaluationDate`` is a **process-global** mutable.
Every QuantLib pricing function in this codebase reads it implicitly — the
``today`` value used to build ``FlatForward`` curves and ``EuropeanExercise``
windows is whatever date is currently set on the singleton when ``NPV()`` is
called. FastAPI runs request handlers concurrently; without serialization,
two requests pricing for different evaluation dates race and the loser gets
its number computed against the winner's date, with no error.

The fix is a single re-entrant lock + a context manager that:

  1. Acquires the lock for the full duration of the pricing call.
  2. Sets the evaluation date inside the locked region.
  3. Restores the previous date on exit.

The lock is ``RLock`` because Greek bump-reprice paths re-enter the engine
inside the same thread (e.g. ``greeks_knockout_ql`` calls
``price_knockout_ql`` six times). RLock allows that without deadlocking.

Also colocates ``_days_from_T`` — previously duplicated verbatim in four
engine modules — so a future change to the rounding policy is one edit.
"""

from __future__ import annotations

import functools
import threading
from contextlib import contextmanager
from typing import Callable, Iterator, Optional, TypeVar

import QuantLib as ql


# Re-entrant so per-thread Greek bump-reprice (which calls back into the same
# engine module) does not deadlock against the outer lock.
_QL_LOCK = threading.RLock()

F = TypeVar("F", bound=Callable)


@contextmanager
def ql_session(evaluation_date: Optional[ql.Date] = None) -> Iterator[ql.Date]:
    """Acquire QL lock, set evaluation date, restore on exit.

    Args:
        evaluation_date: Date to install on ``ql.Settings``. ``None`` means
            "use today's date" — matches the pre-existing behaviour of the
            individual engine modules.

    Yields:
        The ``ql.Date`` actually installed (useful for callers that need to
        compute ``maturity = today + N`` inside the locked region).
    """
    with _QL_LOCK:
        settings = ql.Settings.instance()
        previous = settings.evaluationDate
        target = evaluation_date if evaluation_date is not None else ql.Date.todaysDate()
        try:
            settings.evaluationDate = target
            yield target
        finally:
            settings.evaluationDate = previous


def ql_locked(func: F) -> F:
    """Decorator: acquire the QL lock for the call's duration; restore date on exit.

    Use this on engine-public functions whose bodies set
    ``ql.Settings.instance().evaluationDate`` directly (e.g. via the legacy
    ``_setup_evaluation_date`` helper). The decorator owns the lock + the
    date restoration so the function body can stay un-indented.

    Re-entrant within a single thread (RLock) so Greek bump-reprice paths
    that call price functions inside the same module are safe.
    """
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with _QL_LOCK:
            previous = ql.Settings.instance().evaluationDate
            try:
                return func(*args, **kwargs)
            finally:
                ql.Settings.instance().evaluationDate = previous
    return wrapper  # type: ignore[return-value]


def days_from_T(T: float) -> int:
    """Convert T (years) to integer days. Round-half-up, floor at 1 day.

    See the per-module docstrings (now superseded by this one) for the
    rationale: floor biases T_eff DOWN by up to a full day; banker's
    rounding (Python's built-in ``round``) breaks T = N/2/365 cases. Round-
    half-up gives a consistent ±0.5 day bound.
    """
    return max(int(T * 365.0 + 0.5), 1)
