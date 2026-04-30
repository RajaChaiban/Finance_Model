"""Goldman Sachs ``gs_quant`` engine adapter.

Wires GS's open-source ``gs_quant`` SDK into the Vol Desk router as a
second pricing engine alongside QuantLib. Currently supports European
call / put on equity underliers; extend by adding more constructors
in :func:`_build_instrument`.

Auth contract
-------------
Pricing in ``gs_quant`` is server-side (Marquee). The wrapper:

  * **With credentials** — reads ``GS_MARQUEE_CLIENT_ID`` and
    ``GS_MARQUEE_CLIENT_SECRET`` from the environment and initialises a
    ``GsSession`` once per process. Pricing then runs against the real
    GS market data + models.
  * **Without credentials** — :func:`is_gs_available` returns ``False``
    and any pricing call raises :class:`GsQuantNotConfigured` with a
    pointer to https://marquee.gs.com signup. Callers should branch on
    ``is_gs_available()`` before requesting ``engine="gs"``.

Important contract difference from QuantLib path
------------------------------------------------
The QL adapter takes scalar ``(S, K, r, sigma, T, q)`` inputs and prices
those values directly. The ``gs_quant`` adapter takes ``K``, expiry, and
option_type from the call signature and **uses GS's live market data**
for spot, rates, and vol — your ``S``, ``r``, ``sigma`` arguments are
informational only on this path. This is intentional: the value of
gs_quant is its real-time market view, not a re-pricing of user inputs.

Pass an explicit ``gs_underlier`` kwarg (default ``"SPX"``) to control
which name GS prices.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)


class GsQuantNotConfigured(RuntimeError):
    """Raised when gs_quant pricing is requested without Marquee credentials."""


_SESSION_INITIALISED = False


def is_gs_available() -> bool:
    """True when Marquee credentials are present in the environment.

    Cheap check — does NOT actually open a session. Use this in routers
    or UI to gate the ``engine="gs"`` selector.
    """
    return bool(
        os.getenv("GS_MARQUEE_CLIENT_ID", "").strip()
        and os.getenv("GS_MARQUEE_CLIENT_SECRET", "").strip()
    )


def _ensure_session() -> None:
    """Initialise ``GsSession`` once per process. Idempotent."""
    global _SESSION_INITIALISED
    if _SESSION_INITIALISED:
        return

    if not is_gs_available():
        raise GsQuantNotConfigured(
            "gs_quant pricing requires Marquee credentials. Set "
            "GS_MARQUEE_CLIENT_ID and GS_MARQUEE_CLIENT_SECRET in your "
            ".env. Sign up at https://marquee.gs.com (free for individual "
            "developer access on most data scopes)."
        )

    try:
        from gs_quant.session import GsSession, Environment
    except ImportError as exc:
        raise GsQuantNotConfigured(
            f"gs_quant not installed: {exc}. Run `pip install gs-quant`."
        ) from exc

    GsSession.use(
        Environment.PROD,
        client_id=os.getenv("GS_MARQUEE_CLIENT_ID"),
        client_secret=os.getenv("GS_MARQUEE_CLIENT_SECRET"),
        scopes=("read_product_data",),
    )
    _SESSION_INITIALISED = True
    logger.info("gs_quant: Marquee session initialised")


def _build_european_option(
    strike: float,
    expiry_days: int,
    option_type: str,
    underlier: str = "SPX",
) -> Any:
    """Construct a gs_quant ``EqOption`` for a European call or put."""
    from gs_quant.instrument import EqOption
    from gs_quant.common import OptionType, OptionStyle

    if option_type not in ("call", "put"):
        raise ValueError(f"option_type must be 'call' or 'put', got {option_type!r}")

    return EqOption(
        underlier=underlier,
        strike_price=strike,
        expiration_date=f"{int(expiry_days)}d",
        option_type=OptionType.Call if option_type == "call" else OptionType.Put,
        option_style=OptionStyle.European,
        multiplier=1,
    )


def price_european_gs(
    S: float,
    K: float,
    r: float,
    sigma: float,
    T: float,
    q: float,
    option_type: str = "call",
    gs_underlier: str = "SPX",
    **_kwargs: Any,
) -> Tuple[float, float, Optional[Tuple[float, float]]]:
    """Price a European option via Marquee.

    Returns ``(price, std_err, confidence_interval)`` to match the rest
    of the router's pricer signature. ``std_err`` is ``0.0`` and
    ``confidence_interval`` is ``None`` — Marquee returns a deterministic
    price, not a Monte Carlo sample.

    ``S``, ``r``, ``sigma`` are accepted to match the router signature
    but are NOT passed through — gs_quant uses its own live market data
    for ``gs_underlier``.
    """
    _ensure_session()
    from gs_quant.markets import PricingContext

    expiry_days = max(int(round(T * 365.0)), 1)
    instrument = _build_european_option(
        strike=K,
        expiry_days=expiry_days,
        option_type=option_type,
        underlier=gs_underlier,
    )

    with PricingContext():
        future = instrument.price()
    price = float(future.result())

    logger.info(
        "gs_quant priced %s %s K=%s T=%dd → %.4f",
        gs_underlier, option_type, K, expiry_days, price,
    )
    return price, 0.0, None


def greeks_european_gs(
    S: float,
    K: float,
    r: float,
    sigma: float,
    T: float,
    q: float,
    option_type: str = "call",
    gs_underlier: str = "SPX",
    **_kwargs: Any,
) -> Dict[str, float]:
    """Greeks via Marquee for a European option.

    Conventions match the rest of the codebase:
      * delta — per 1 unit of spot
      * gamma — per 1 unit of spot, second-order
      * vega — per 1% absolute σ (we rescale gs_quant's per-1-vol-point)
      * theta — per calendar day (gs_quant returns per year, we rescale)
      * rho — per 1% absolute r (we rescale gs_quant's per-1.0-rate)
    """
    _ensure_session()
    from gs_quant.markets import PricingContext
    from gs_quant.risk import EqDelta, EqGamma, EqVega, EqTheta, EqRho

    expiry_days = max(int(round(T * 365.0)), 1)
    instrument = _build_european_option(
        strike=K,
        expiry_days=expiry_days,
        option_type=option_type,
        underlier=gs_underlier,
    )

    measures = {
        "delta": EqDelta,
        "gamma": EqGamma,
        "vega": EqVega,
        "theta": EqTheta,
        "rho": EqRho,
    }

    with PricingContext():
        futures = {name: instrument.calc(measure) for name, measure in measures.items()}

    raw = {name: float(fut.result()) for name, fut in futures.items()}

    return {
        "price": float(instrument.price().result()),
        "delta": raw["delta"],
        "gamma": raw["gamma"],
        # gs_quant vega is per 1.0 vol-point; rescale to per 1% to match repo convention.
        "vega": raw["vega"] / 100.0,
        # gs_quant theta is per year; rescale to per calendar day.
        "theta": raw["theta"] / 365.0,
        # gs_quant rho is per 1.0 rate change; rescale to per 1%.
        "rho": raw["rho"] / 100.0,
    }
