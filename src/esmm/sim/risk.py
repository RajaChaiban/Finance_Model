"""Pre-trade and post-trade risk enforcement.

A real desk dies from skipping these checks. The sim defaults to the
discipline a regulated MM would impose.

Pre-trade (per order, called before the order reaches the LOB):

* ``max_notional_usd`` — gross open notional (sum of |qty| * mark)
* ``max_net_delta`` — directional exposure (signed sum of qty * delta)
* ``max_gross_gamma`` — convexity exposure (phase 4 / options only;
  ``0`` disables the check)
* ``concentration_pct`` — single-symbol % of gross notional cap
* ``max_orders_per_sec`` — request rate limit per participant

Post-trade (continuous, called after fills / mark-to-market):

* ``daily_loss_kill_switch_usd`` — flat the book, stop quoting
* ``max_drawdown_pct`` — soft warn at 50%, halt at 75% of the cap
* ``inventory_age_sec`` — emit a stale-position alert

When a breach occurs the engine appends a :class:`RiskBreach` to
``breaches`` and the *caller* decides whether to drop the order, halt
quoting, or kill the session. Daily-loss and hard drawdown trip the
kill-switch automatically: ``halted`` flips to ``True`` and all
subsequent pretrade checks reject.

Phase 3 will wire this into the kernel + arena. The implementation here
is fully tested in isolation so Phase 3 can rely on it.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Deque, Optional


@dataclass
class RiskLimits:
    """All limits expressed as positive thresholds.

    Setting a limit to ``math.inf`` (or a very large number) disables it.
    Setting ``max_gross_gamma=0`` disables gamma checks entirely (used
    for equities-only phase-1/2/3 mode).
    """

    max_notional_usd: float = 1_000_000.0
    max_net_delta: float = 10_000.0
    max_gross_gamma: float = 0.0  # 0 disables (phase 4)
    concentration_pct: float = 0.4
    max_orders_per_sec: int = 50
    daily_loss_kill_switch_usd: float = 50_000.0
    max_drawdown_pct: float = 0.75
    inventory_age_sec: float = 3600.0

    def __post_init__(self) -> None:
        for name in (
            "max_notional_usd",
            "max_net_delta",
            "concentration_pct",
            "daily_loss_kill_switch_usd",
            "max_drawdown_pct",
            "inventory_age_sec",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be >= 0; got {getattr(self, name)}")
        if self.max_gross_gamma < 0:
            raise ValueError("max_gross_gamma must be >= 0")
        if self.max_orders_per_sec <= 0:
            raise ValueError("max_orders_per_sec must be > 0")
        if not (0.0 <= self.concentration_pct <= 1.0):
            raise ValueError("concentration_pct must be in [0, 1]")
        if not (0.0 <= self.max_drawdown_pct <= 1.0):
            raise ValueError("max_drawdown_pct must be in [0, 1]")


@dataclass
class RiskBreach:
    """A single limit breach event.

    ``actual_value`` is the value that violated; ``threshold`` is the
    limit that was exceeded. ``severity`` is informational:

    * ``warn`` — soft breach (logged, order may still proceed)
    * ``block`` — hard pre-trade rejection
    * ``halt`` — kill-switch tripped, all activity stops
    """

    ts: float
    limit_name: str
    actual_value: float
    threshold: float
    severity: str = "block"
    symbol: str | None = None
    participant_id: str | None = None


@dataclass
class RiskState:
    """Live counters fed into the risk checks.

    These are produced by the caller (kernel / arena) from the LOB and
    inventory book. The risk engine is stateless about the world — it
    only owns its breach log and the halt flag.
    """

    ts: float
    gross_notional_usd: float = 0.0
    net_delta: float = 0.0
    gross_gamma: float = 0.0
    daily_pnl: float = 0.0
    peak_daily_pnl: float = 0.0
    # symbol → (gross_notional, oldest_position_ts)
    per_symbol_notional: dict[str, float] = field(default_factory=dict)
    per_symbol_oldest_ts: dict[str, float] = field(default_factory=dict)


class RiskEngine:
    """Pre-trade and post-trade risk gate.

    Usage::

        engine = RiskEngine(RiskLimits())
        allowed, breach = engine.check_pretrade(
            participant_id="mm1", symbol="SPY",
            order_notional_usd=15_000, order_delta=100,
            state=current_state,
        )
        if not allowed:
            ...  # caller decides: drop, throttle, or halt

    The engine is per-session; create a new one per backtest run.
    """

    def __init__(self, limits: RiskLimits) -> None:
        self.limits = limits
        self.breaches: list[RiskBreach] = []
        self.halted: bool = False
        # Rolling 1-second window of order timestamps per participant.
        self._order_window: dict[str, Deque[float]] = {}

    # ------------------------------------------------------------------
    # Halt control
    # ------------------------------------------------------------------
    @property
    def is_halted(self) -> bool:
        return self.halted

    def halt(self, ts: float, reason: str, actual: float, threshold: float) -> RiskBreach:
        """Trip the kill-switch. Returns the breach record."""
        self.halted = True
        breach = RiskBreach(
            ts=ts,
            limit_name=reason,
            actual_value=actual,
            threshold=threshold,
            severity="halt",
        )
        self.breaches.append(breach)
        return breach

    def reset(self) -> None:
        """Clear breaches and un-halt. Useful for restarts in tests."""
        self.breaches.clear()
        self.halted = False
        self._order_window.clear()

    # ------------------------------------------------------------------
    # Pre-trade gate
    # ------------------------------------------------------------------
    def check_pretrade(
        self,
        *,
        participant_id: str,
        symbol: str,
        order_notional_usd: float,
        order_delta: float,
        order_gamma: float = 0.0,
        state: RiskState,
    ) -> tuple[bool, Optional[RiskBreach]]:
        """Return ``(allowed, breach_or_None)``.

        ``state`` is the firm's current state *before* this order
        executes. The caller is responsible for computing it from the
        inventory book and LOB.
        """
        if self.halted:
            b = self._mk_breach(state.ts, "halted", 1.0, 0.0, symbol, participant_id)
            return False, b

        # Rate limit per participant (rolling 1-second window).
        if not self._rate_limit_ok(participant_id, state.ts):
            n = len(self._order_window[participant_id])
            b = self._mk_breach(
                state.ts,
                "max_orders_per_sec",
                float(n),
                float(self.limits.max_orders_per_sec),
                symbol,
                participant_id,
            )
            return False, b

        # Notional cap.
        new_notional = state.gross_notional_usd + abs(order_notional_usd)
        if new_notional > self.limits.max_notional_usd:
            b = self._mk_breach(
                state.ts,
                "max_notional_usd",
                new_notional,
                self.limits.max_notional_usd,
                symbol,
                participant_id,
            )
            return False, b

        # Net delta cap.
        new_delta = state.net_delta + order_delta
        if abs(new_delta) > self.limits.max_net_delta:
            b = self._mk_breach(
                state.ts,
                "max_net_delta",
                new_delta,
                self.limits.max_net_delta,
                symbol,
                participant_id,
            )
            return False, b

        # Gamma cap — only if enabled (phase 4).
        if self.limits.max_gross_gamma > 0:
            new_gamma = state.gross_gamma + abs(order_gamma)
            if new_gamma > self.limits.max_gross_gamma:
                b = self._mk_breach(
                    state.ts,
                    "max_gross_gamma",
                    new_gamma,
                    self.limits.max_gross_gamma,
                    symbol,
                    participant_id,
                )
                return False, b

        # Concentration cap — single-symbol % of gross.
        sym_notional = state.per_symbol_notional.get(symbol, 0.0) + abs(order_notional_usd)
        if new_notional > 0 and sym_notional / new_notional > self.limits.concentration_pct:
            b = self._mk_breach(
                state.ts,
                "concentration_pct",
                sym_notional / new_notional,
                self.limits.concentration_pct,
                symbol,
                participant_id,
            )
            return False, b

        # All checks passed — record the order for the rate window.
        self._record_order(participant_id, state.ts)
        return True, None

    # ------------------------------------------------------------------
    # Post-trade gate
    # ------------------------------------------------------------------
    def check_posttrade(self, state: RiskState) -> Optional[RiskBreach]:
        """Inspect post-fill state. Returns the *worst* breach, or None.

        Side-effect: may trip ``halted`` for daily-loss / hard-drawdown.
        """
        if self.halted:
            return None  # nothing new to report; already halted

        # 1) Daily loss kill-switch (hard halt).
        if state.daily_pnl < 0 and abs(state.daily_pnl) >= self.limits.daily_loss_kill_switch_usd:
            return self.halt(
                state.ts,
                "daily_loss_kill_switch_usd",
                state.daily_pnl,
                -self.limits.daily_loss_kill_switch_usd,
            )

        # 2) Drawdown (soft warn at 50% of the cap, hard halt at 100%).
        dd = self._drawdown_pct(state)
        if dd >= self.limits.max_drawdown_pct:
            return self.halt(state.ts, "max_drawdown_pct", dd, self.limits.max_drawdown_pct)
        if dd >= 0.5 * self.limits.max_drawdown_pct and self.limits.max_drawdown_pct > 0:
            return self._record_warn(
                state.ts, "max_drawdown_pct", dd, self.limits.max_drawdown_pct
            )

        # 3) Stale inventory.
        for sym, oldest_ts in state.per_symbol_oldest_ts.items():
            age = state.ts - oldest_ts
            if age >= self.limits.inventory_age_sec:
                return self._record_warn(
                    state.ts,
                    "inventory_age_sec",
                    age,
                    self.limits.inventory_age_sec,
                    symbol=sym,
                )

        return None

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _rate_limit_ok(self, participant_id: str, ts: float) -> bool:
        window = self._order_window.setdefault(participant_id, deque())
        cutoff = ts - 1.0
        while window and window[0] < cutoff:
            window.popleft()
        return len(window) < self.limits.max_orders_per_sec

    def _record_order(self, participant_id: str, ts: float) -> None:
        self._order_window.setdefault(participant_id, deque()).append(ts)

    @staticmethod
    def _drawdown_pct(state: RiskState) -> float:
        # Drawdown is only meaningful relative to a positive peak. Without
        # one, treat drawdown as 0% — the daily-loss kill-switch is the
        # right guard for "you're losing money from zero."
        peak = state.peak_daily_pnl
        if peak <= 0:
            return 0.0
        return max(0.0, (peak - state.daily_pnl) / peak)

    def _mk_breach(
        self,
        ts: float,
        name: str,
        actual: float,
        threshold: float,
        symbol: str | None,
        participant_id: str | None,
    ) -> RiskBreach:
        b = RiskBreach(
            ts=ts,
            limit_name=name,
            actual_value=actual,
            threshold=threshold,
            severity="block",
            symbol=symbol,
            participant_id=participant_id,
        )
        self.breaches.append(b)
        return b

    def _record_warn(
        self,
        ts: float,
        name: str,
        actual: float,
        threshold: float,
        symbol: str | None = None,
    ) -> RiskBreach:
        b = RiskBreach(
            ts=ts,
            limit_name=name,
            actual_value=actual,
            threshold=threshold,
            severity="warn",
            symbol=symbol,
        )
        self.breaches.append(b)
        return b


__all__ = ["RiskLimits", "RiskBreach", "RiskEngine", "RiskState"]
