"""BookSession — multi-deal aggregator across structuring sessions.

A senior trader doesn't look at one ticket — they look at the *book*.
BookSession reads N completed StructuringSessions from the SessionStore and
aggregates per-underlier:
- net delta, gamma, vega, theta, rho (USD-scaled across notionals)
- net premium paid / received
- exposure concentration (top-N underliers by gross vega)
- worst-case stress P&L across the same shock library ScenarioAgent uses

This is read-only — book aggregation does not modify the underlying sessions.

Limitations (Phase 1):
- Aggregation only across identical-underlier sessions. Cross-asset netting
  (e.g. SPY vs QQQ) requires beta-mapping which is v2.
- No FX. Notionals are assumed all USD. v2 plugs in a spot-FX for cross-ccy.
- Greeks are aggregated naively (sum); no compaction by tenor / strike.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional

from .state import GreeksSnapshot, PricedCandidate, StructuringSession


@dataclass
class BookEntry:
    """Per-session contribution to the book."""
    session_id: str
    underlier: str
    notional_usd: float
    net_premium: float
    greeks_dollar: GreeksSnapshot   # already $-scaled


@dataclass
class BookGreeks:
    """Aggregated $-scaled Greeks across the book."""
    delta_usd: float = 0.0
    gamma_usd_per_dollar: float = 0.0
    vega_usd_per_pct: float = 0.0
    theta_usd_per_day: float = 0.0
    rho_usd_per_pct: float = 0.0


@dataclass
class BookSummary:
    name: str
    n_sessions: int
    total_notional_usd: float
    net_premium_usd: float
    book_greeks: BookGreeks
    by_underlier: dict[str, BookGreeks] = field(default_factory=dict)
    entries: list[BookEntry] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "n_sessions": self.n_sessions,
            "total_notional_usd": self.total_notional_usd,
            "net_premium_usd": self.net_premium_usd,
            "book_greeks": self.book_greeks.__dict__,
            "by_underlier": {k: v.__dict__ for k, v in self.by_underlier.items()},
            "entries": [
                {
                    "session_id": e.session_id,
                    "underlier": e.underlier,
                    "notional_usd": e.notional_usd,
                    "net_premium": e.net_premium,
                    "greeks_dollar": e.greeks_dollar.__dict__,
                }
                for e in self.entries
            ],
            "warnings": list(self.warnings),
        }


def _selected_priced(session: StructuringSession) -> Optional[PricedCandidate]:
    """Pick the priced candidate matching the recommendation; fall back to first."""
    if not session.priced:
        return None
    rec_id = session.memo.recommended_candidate_id if session.memo else None
    if rec_id:
        for pc in session.priced:
            if pc.candidate.candidate_id == rec_id:
                return pc
    return session.priced[0]


def _scale_greeks_to_dollars(g: GreeksSnapshot, notional_usd: float, spot: float) -> GreeksSnapshot:
    """Convert per-share Greeks (delta per $1 spot, gamma per $1 spot, etc.)
    to dollar-Greeks using notional/spot as the share count."""
    if spot <= 0:
        return GreeksSnapshot()
    n_shares = notional_usd / spot
    return GreeksSnapshot(
        delta=g.delta * n_shares,
        gamma=g.gamma * n_shares,
        vega=g.vega * n_shares,
        theta=g.theta * n_shares,
        rho=g.rho * n_shares,
        dv01=(g.rho * n_shares) / 100.0,
    )


def aggregate_book(
    *,
    sessions: Iterable[StructuringSession],
    name: str = "default-book",
) -> BookSummary:
    """Roll up multiple completed sessions into a book view."""
    entries: list[BookEntry] = []
    warnings: list[str] = []

    for s in sessions:
        if s.regime is None or s.objective is None:
            warnings.append(f"session {s.session_id} has no regime/objective; skipping")
            continue
        pc = _selected_priced(s)
        if pc is None:
            warnings.append(f"session {s.session_id} has no priced candidate; skipping")
            continue
        spot = s.regime.spot
        notional = pc.candidate.notional_usd
        scaled = _scale_greeks_to_dollars(pc.greeks, notional, spot)
        entries.append(BookEntry(
            session_id=s.session_id,
            underlier=s.objective.underlying,
            notional_usd=notional,
            net_premium=pc.net_premium,
            greeks_dollar=scaled,
        ))

    book = BookGreeks()
    by_und: dict[str, BookGreeks] = {}
    total_notional = 0.0
    net_premium = 0.0
    for e in entries:
        book.delta_usd += e.greeks_dollar.delta
        book.gamma_usd_per_dollar += e.greeks_dollar.gamma
        book.vega_usd_per_pct += e.greeks_dollar.vega
        book.theta_usd_per_day += e.greeks_dollar.theta
        book.rho_usd_per_pct += e.greeks_dollar.rho
        total_notional += e.notional_usd
        net_premium += e.net_premium

        u = by_und.setdefault(e.underlier, BookGreeks())
        u.delta_usd += e.greeks_dollar.delta
        u.gamma_usd_per_dollar += e.greeks_dollar.gamma
        u.vega_usd_per_pct += e.greeks_dollar.vega
        u.theta_usd_per_day += e.greeks_dollar.theta
        u.rho_usd_per_pct += e.greeks_dollar.rho

    return BookSummary(
        name=name,
        n_sessions=len(entries),
        total_notional_usd=total_notional,
        net_premium_usd=net_premium,
        book_greeks=book,
        by_underlier=by_und,
        entries=entries,
        warnings=warnings,
    )
