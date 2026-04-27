"""Rate convention conversions.

Every market-quoted interest rate carries an implicit (compounding, day-count)
convention. The pricing engines (Black-Scholes, binomial trees, MC) expect a
*continuously compounded rate on Actual/365 Fixed* — anything else must be
converted at the data-ingestion boundary.

This module provides one-way conversions FROM common market quote conventions
TO the engine-expected convention. Apply once, at the boundary; downstream
code stays oblivious.

References:
- Hull, "Options, Futures, and Other Derivatives", 10e, Ch.4 (compounding conversions)
- ISDA 2006 Definitions, §4.16 (day-count fractions)
- US Treasury Bureau of the Fiscal Service: bill/note quote conventions
"""

from enum import Enum
import numpy as np


class QuoteBasis(Enum):
    """Convention under which a rate is quoted."""

    # Already what the engine wants — no conversion needed
    CONTINUOUS_ACT365 = "continuous_act365"

    # Money-market simple-interest quotes (no compounding within the period)
    ACT360_SIMPLE = "act360_simple"   # USD: SOFR, EFFR, EURIBOR, USD LIBOR (legacy)
    ACT365_SIMPLE = "act365_simple"   # GBP: SONIA, GBP LIBOR (legacy); JPY TONA

    # Treasury bill discount basis (price-quoted, not yield-quoted)
    ACT360_DISCOUNT = "act360_discount"   # US T-bills: BEY published from this

    # Bond yields (semi-annual compounding)
    BEY_SEMIANNUAL = "bey_semiannual"     # US Treasury notes/bonds, ^TNX, ^TYX

    # Annual compounding (rare for USD; some EUR govts)
    ANNUAL_COMPOUNDED = "annual_compounded"


def to_continuous_act365(quoted_rate: float, basis: QuoteBasis) -> float:
    """Convert a market-quoted rate to continuously compounded Actual/365.

    The conversion treats the quote as an *annualized* rate (which all the
    bases above are, by construction) and produces the continuous rate that
    would yield the same growth over one year.

    Args:
        quoted_rate: The rate as quoted by the data source (decimal, e.g. 0.0530)
        basis: The convention under which it was quoted

    Returns:
        Continuously compounded rate on Act/365 — the value to feed into the engine

    Examples:
        >>> # SOFR quoted at 5.30% (Act/360 simple)
        >>> to_continuous_act365(0.0530, QuoteBasis.ACT360_SIMPLE)
        0.052340...
        >>> # 10Y Treasury yield 4.30% (BEY)
        >>> to_continuous_act365(0.0430, QuoteBasis.BEY_SEMIANNUAL)
        0.042543...
    """
    r = quoted_rate

    if basis == QuoteBasis.CONTINUOUS_ACT365:
        return r

    if basis == QuoteBasis.ACT360_SIMPLE:
        # Annualized growth factor = 1 + r * (365/360); then continuous = ln(growth)
        return float(np.log1p(r * 365.0 / 360.0))

    if basis == QuoteBasis.ACT365_SIMPLE:
        return float(np.log1p(r))

    if basis == QuoteBasis.ACT360_DISCOUNT:
        # Discount yield d relates to price: P = 1 - d * (days/360)
        # Convert d → money-market yield (act/360 simple) → continuous
        # For 1Y: P = 1 - d ⇒ growth = 1/P = 1/(1-d) ⇒ r_mm_act360 = (1/(1-d) - 1)
        # That r_mm is already on Act/360 simple basis; chain through:
        if r >= 1.0:
            raise ValueError(f"Discount yield must be < 1.0, got {r}")
        r_mm_act360 = r / (1.0 - r)
        return float(np.log1p(r_mm_act360 * 365.0 / 360.0))

    if basis == QuoteBasis.BEY_SEMIANNUAL:
        # Bond-equivalent yield compounds semi-annually:
        # annual growth = (1 + r/2)^2 ⇒ continuous = 2 * ln(1 + r/2)
        return float(2.0 * np.log1p(r / 2.0))

    if basis == QuoteBasis.ANNUAL_COMPOUNDED:
        return float(np.log1p(r))

    raise ValueError(f"Unsupported quote basis: {basis}")


def treasury_basis_for_tenor_days(days: int) -> QuoteBasis:
    """Return the quote basis that Yahoo/Bloomberg uses for a US Treasury tenor.

    - ≤ 365 days: T-bills (^IRX), quoted as money-market yield on Act/360.
      Strictly Yahoo's ^IRX reports the *discount* yield, but for sub-1Y SOFR-
      adjacent uses the simple act/360 interpretation gives sub-bp differences
      and is the more common practitioner shorthand. Use ACT360_DISCOUNT if
      precision matters at the tenor boundary.
    - > 365 days: T-notes/bonds (^TNX 10Y, ^TYX 30Y), quoted as bond-equivalent
      yield (semi-annual compounding).
    """
    if days <= 365:
        return QuoteBasis.ACT360_SIMPLE
    return QuoteBasis.BEY_SEMIANNUAL


def annualised_to_period_discount_factor(r_continuous: float, T_years: float) -> float:
    """Convenience: discount factor exp(-rT) using engine-consistent basis."""
    return float(np.exp(-r_continuous * T_years))
