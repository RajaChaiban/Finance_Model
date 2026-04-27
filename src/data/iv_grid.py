"""Implied-volatility grid built by inverting an option-chain mid price-by-price.

Each chain row is inverted to its European-Black-Scholes implied vol via
``solver.solve_for_volatility_european`` — the same inverter the rest of the
pipeline uses, so the convention matches the exchange-quote convention
(listed-option IV is BS-defined regardless of exercise style).

Rows that fail to invert (sub-intrinsic mid, no-arb violation, illiquid wing)
are logged and skipped. The grid is the *raw* market surface — no smoothing,
no SVI, no arbitrage repair. That fit step is left for a future iteration.

Layout:
    IVGrid.strikes  : sorted ndarray, shape (N_K,)
    IVGrid.expiries : sorted ndarray of T-in-years, shape (N_T,)
    IVGrid.iv       : ndarray, shape (N_T, N_K). NaN where a (T, K) point
                      had no usable quote; downstream surface builder will
                      forward-fill / back-fill along strike.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.engines.solver import solve_for_volatility_european

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IVGrid:
    """Strike × expiry grid of implied volatilities."""

    strikes: np.ndarray
    expiries: np.ndarray  # T in years, ascending
    iv: np.ndarray        # shape (len(expiries), len(strikes))
    success_rate: float
    n_quotes_total: int
    n_quotes_inverted: int

    def __post_init__(self) -> None:
        if self.iv.shape != (len(self.expiries), len(self.strikes)):
            raise ValueError(
                f"IVGrid.iv shape {self.iv.shape} inconsistent with "
                f"({len(self.expiries)}, {len(self.strikes)})"
            )
        if not np.all(np.diff(self.expiries) > 0):
            raise ValueError("IVGrid.expiries must be strictly ascending")
        if not np.all(np.diff(self.strikes) > 0):
            raise ValueError("IVGrid.strikes must be strictly ascending")


def _invert_quote(
    S: float,
    K: float,
    target_price: float,
    r: float,
    T: float,
    q: float,
    option_type: str,
) -> Optional[float]:
    """Invert a single mid quote to IV. Returns None on failure."""
    try:
        result = solve_for_volatility_european(
            S=S,
            K=K,
            target_price=float(target_price),
            r=r,
            T=T,
            q=q,
            option_type=option_type,
        )
        if not result.converged:
            return None
        sigma = float(result.value)
        if not np.isfinite(sigma) or sigma <= 0.0 or sigma >= 5.0:
            return None
        return sigma
    except (ValueError, RuntimeError) as exc:
        logger.debug(
            f"IV inversion failed at K={K:.2f} T={T:.4f} {option_type}: {exc}"
        )
        return None


def build_iv_grid(
    chain: Dict[date, pd.DataFrame],
    S: float,
    r: float,
    q: float,
    today: Optional[date] = None,
    min_success_rate: float = 0.6,
) -> IVGrid:
    """Build an IV grid from a cleaned option chain.

    Args:
        chain: Output of ``market_data.fetch_option_chain`` — dict[expiry → DataFrame].
        S: Spot price (used as the BS reference).
        r: Continuously compounded risk-free rate, Act/365 (already converted).
        q: Continuous dividend yield.
        today: Reference date for T computation (None → today).
        min_success_rate: Reject the build if fewer than this fraction of
            quotes invert. Default 60 % is a deliberately loose floor —
            wing illiquidity routinely kills 20-30 % even on SPY.

    Returns:
        IVGrid with strict-ascending strikes/expiries and an iv matrix.
        NaN cells are allowed (filled by the surface builder).

    Raises:
        ValueError: If the chain is empty or the success rate is below
            ``min_success_rate`` (means the surface cannot be trusted).
    """
    if not chain:
        raise ValueError("Cannot build IV grid: option chain is empty")

    today_date = today if today is not None else date.today()

    rows: List[Tuple[float, float, float]] = []  # (T, K, iv)
    n_total = 0
    n_ok = 0

    for expiry_date, df in chain.items():
        T = (expiry_date - today_date).days / 365.0
        if T <= 0:
            continue

        # Per-strike preference: pick the OTM side (calls for K>S, puts for K<S).
        # OTM quotes are more liquid and don't need to wash out the intrinsic
        # before the time value shows up in the BS inversion.
        for K_val, sub in df.groupby("strike", sort=True):
            n_total += 1
            preferred = sub[sub["option_type"] == ("call" if K_val >= S else "put")]
            chosen = preferred if len(preferred) else sub
            row = chosen.iloc[0]
            iv = _invert_quote(
                S=S,
                K=float(K_val),
                target_price=float(row["mid"]),
                r=r,
                T=T,
                q=q,
                option_type=str(row["option_type"]),
            )
            if iv is not None:
                rows.append((T, float(K_val), iv))
                n_ok += 1

    if n_total == 0:
        raise ValueError("IV grid: no quotes survived chain iteration")

    success_rate = n_ok / n_total
    if success_rate < min_success_rate:
        raise ValueError(
            f"IV inversion success rate {success_rate:.1%} below floor "
            f"{min_success_rate:.0%}; refusing to build untrustworthy surface."
        )

    # Densify into a (T, K) matrix with NaN holes.
    expiries = np.array(sorted({t for t, _, _ in rows}))
    strikes = np.array(sorted({k for _, k, _ in rows}))
    iv_matrix = np.full((len(expiries), len(strikes)), np.nan)
    t_idx = {t: i for i, t in enumerate(expiries)}
    k_idx = {k: j for j, k in enumerate(strikes)}
    for T, K, iv in rows:
        iv_matrix[t_idx[T], k_idx[K]] = iv

    if len(strikes) < 2 or len(expiries) < 2:
        raise ValueError(
            f"IV grid too sparse: {len(strikes)} strikes × {len(expiries)} "
            f"expiries (need ≥ 2 each for surface fit)."
        )

    logger.info(
        f"Built IV grid: {len(strikes)} strikes × {len(expiries)} expiries, "
        f"{n_ok}/{n_total} quotes inverted ({success_rate:.0%})."
    )

    return IVGrid(
        strikes=strikes,
        expiries=expiries,
        iv=iv_matrix,
        success_rate=success_rate,
        n_quotes_total=n_total,
        n_quotes_inverted=n_ok,
    )
