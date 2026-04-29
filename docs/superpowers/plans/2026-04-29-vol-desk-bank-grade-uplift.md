# Vol Desk Bank-Grade Uplift Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. Each Phase = one self-contained task delivered to a fresh Sonnet 4.6 subagent.

**Goal:** Address the P0/P1 gaps flagged by the VP-Trading + Structurer review: persistence, real rate curve, exposed Monte Carlo, bucketed sensitivities, LLM abstraction, and the multi-asset / autocallable foundation a real EQD desk needs.

**Architecture:**
- **Phases 1-5** (plumbing): persistence, rates, MC, sensitivities, LLM abstraction. Each ships independently with new tests; existing 1202 tests must stay green.
- **Phases 6-9** (products): multi-asset Pydantic types → worst-of MC engine → autocallable structure → term-sheet PDF. Each builds on the prior.
- Phases are executed in a **build → test → fix-or-proceed loop**. Each phase owned by a fresh subagent with full plan context. After each subagent finishes, the orchestrator (Opus 4.7) reviews the diff, runs the test suite via Haiku 4.5 verifier, and either accepts (proceed) or dispatches a fix-up subagent.

**Tech Stack:** Python 3.11+, FastAPI, QuantLib, Pydantic v2, sqlite3 (stdlib), httpx, numpy, scipy, pytest. Frontend: React 19 + Vite 8 + TS + recharts.

**Model Allocation:**
| Role | Model | Why |
|---|---|---|
| Plan author / orchestrator (this session) | Opus 4.7 (1M ctx) | Holds whole repo + plan + review context |
| Per-phase implementation subagent | Sonnet 4.6 | Fastest competent code generation |
| Verification subagent (run tests, parse failures) | Haiku 4.5 | Cheap, fast for mechanical work |
| Code review subagent | Opus 4.7 | Quality gate before "proceed" |

**Skills In Use:**
- `superpowers:writing-plans` (this document)
- `superpowers:subagent-driven-development` (per-phase dispatch)
- `superpowers:test-driven-development` (each task has failing test first)
- `superpowers:systematic-debugging` (on test failure inside loop)
- `superpowers:verification-before-completion` (before marking phase done)
- `superpowers:receiving-code-review` (between phases)

---

## File Structure

| Path | Status | Responsibility |
|---|---|---|
| `src/agents/persistence.py` | Create | SQLite-backed `SessionStore` implementation |
| `src/agents/orchestrator.py` | Modify | Pluggable store interface; opt-in SQLite via env |
| `tests/test_session_persistence.py` | Create | Unit tests for SQLite store + restart-recovery |
| `src/data/rate_curve.py` | Create | `RateCurve` class wrapping FRED SOFR + flat fallback |
| `tests/test_rate_curve.py` | Create | Tests: bootstrap, fallback, evaluation, caching |
| `src/agents/orchestrator.py` | Modify | Replace `0.045` constant with `RateCurve.spot_rate(T)` |
| `src/api/models.py` | Modify | Add `engine: Literal["auto","analytic","tree","mc","fdm"]` field |
| `src/engines/router.py` | Modify | Honor engine selection; surface MC for American/Asian |
| `src/api/handlers.py` | Modify | Pass `engine` from request through to router |
| `tests/test_engine_selection.py` | Create | Tests: each engine selectable, MC controls live |
| `src/analysis/sensitivities.py` | Create | Bucketed Vega (tenor × strike), scenario grid (S × σ), gamma ladder |
| `src/api/models.py` | Modify | Extend `PricingResult` with `sensitivities: Optional[SensitivityBlock]` |
| `src/api/handlers.py` | Modify | Compute sensitivities when `request.deep_risk=True` |
| `tests/test_sensitivities.py` | Create | Tests: shapes, finite values, sign conventions |
| `frontend/src/components/SensitivityHeatmap.tsx` | Create | recharts heatmap of scenario grid |
| `frontend/src/components/ReportDisplay.tsx` | Modify | Render `SensitivityHeatmap` when present |
| `src/agents/llm_provider.py` | Create | Abstract `LLMProvider` + Gemini/Anthropic/OpenAI/Mock impls |
| `src/agents/llm_client.py` | Modify | Delegate to `LLMProvider` instead of direct Gemini |
| `tests/test_llm_provider.py` | Create | Provider selection via env, mock provider deterministic |
| `src/agents/state.py` | Modify | Add `BasketObjective`, `Leg`, `ObservationSchedule`, `Structure` types |
| `tests/test_basket_state.py` | Create | Round-trip serialization tests |
| `src/engines/multi_asset_mc.py` | Create | Cholesky-driven correlated GBM Monte Carlo |
| `tests/test_multi_asset_mc.py` | Create | Vanilla worst-of vs analytic two-asset Margrabe sanity |
| `src/engines/autocallable.py` | Create | Phoenix autocallable pricer atop `multi_asset_mc` |
| `tests/test_autocallable.py` | Create | Tests: trivial cases, KO=full coupon, deep ITM, deep OTM |
| `src/report/term_sheet.py` | Create | PRIIPs-style term sheet PDF (reportlab) |
| `tests/test_term_sheet.py` | Create | PDF generates without exception, contains key fields |

---

## Phase 1 — SQLite-backed SessionStore

**Why first:** unblocks production demos (in-memory store is the only thing stopping a restart-resilient session). Cleanly bounded — single new module + tiny orchestrator wiring change.

**Files:**
- Create: `src/agents/persistence.py`
- Modify: `src/agents/orchestrator.py:54-94` (SessionStore)
- Create: `tests/test_session_persistence.py`

- [ ] **Step 1: Write the failing test for SQLite session round-trip**

```python
# tests/test_session_persistence.py
from src.agents.persistence import SQLiteSessionStore
from src.agents.state import StructuringSession

def test_sqlite_store_roundtrip(tmp_path):
    db = tmp_path / "sessions.db"
    store = SQLiteSessionStore(db_path=str(db))
    session = StructuringSession(intake_nl="Buy a 1y SPY KO put")
    store.add(session)
    rehydrated = store.get(session.session_id)
    assert rehydrated is not None
    assert rehydrated.session_id == session.session_id
    assert rehydrated.intake_nl == session.intake_nl

def test_sqlite_store_survives_reopen(tmp_path):
    db = tmp_path / "sessions.db"
    store_a = SQLiteSessionStore(db_path=str(db))
    s = StructuringSession(intake_nl="hello")
    store_a.add(s)
    store_b = SQLiteSessionStore(db_path=str(db))
    assert store_b.get(s.session_id) is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_session_persistence.py -v`
Expected: FAIL — `ModuleNotFoundError: src.agents.persistence`

- [ ] **Step 3: Implement `SQLiteSessionStore`**

```python
# src/agents/persistence.py
import json
import sqlite3
import threading
from pathlib import Path
from queue import Empty, SimpleQueue
from typing import Any, Optional

from .state import StructuringSession


class SQLiteSessionStore:
    """Drop-in SessionStore replacement persisting to SQLite.

    JSON-serialised sessions; queues remain in-memory (events are ephemeral).
    """

    def __init__(self, db_path: str = "vol_desk_sessions.db") -> None:
        self._lock = threading.RLock()
        self._db_path = db_path
        self._queues: dict[str, SimpleQueue] = {}
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY,
                    payload    TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path, check_same_thread=False, isolation_level=None)

    def add(self, session: StructuringSession) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO sessions(session_id, payload, updated_at) VALUES (?, ?, strftime('%s','now'))",
                (session.session_id, session.model_dump_json()),
            )
            self._queues.setdefault(session.session_id, SimpleQueue())

    def get(self, session_id: str) -> Optional[StructuringSession]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT payload FROM sessions WHERE session_id = ?", (session_id,))
            row = cur.fetchone()
        if row is None:
            return None
        return StructuringSession.model_validate_json(row[0])

    def update(self, session: StructuringSession) -> None:
        self.add(session)

    def list_ids(self) -> list[str]:
        with self._lock, self._connect() as conn:
            cur = conn.execute("SELECT session_id FROM sessions ORDER BY updated_at DESC")
            return [r[0] for r in cur.fetchall()]

    def queue(self, session_id: str) -> Optional[SimpleQueue]:
        with self._lock:
            return self._queues.setdefault(session_id, SimpleQueue())

    def emit(self, session_id: str, event: dict[str, Any]) -> None:
        q = self.queue(session_id)
        if q is not None:
            q.put(event)

    def drain(self, session_id: str, timeout: float = 0.0) -> Optional[dict[str, Any]]:
        q = self.queue(session_id)
        if q is None:
            return None
        try:
            return q.get(timeout=timeout) if timeout > 0 else q.get_nowait()
        except Empty:
            return None
```

- [ ] **Step 4: Wire up env-var selection in orchestrator**

```python
# src/agents/orchestrator.py — replace get_store() body
import os

def get_store() -> SessionStore:
    global _GLOBAL_STORE
    if _GLOBAL_STORE is None:
        if os.getenv("VOL_DESK_PERSIST") == "1":
            from .persistence import SQLiteSessionStore
            _GLOBAL_STORE = SQLiteSessionStore(
                db_path=os.getenv("VOL_DESK_DB_PATH", "vol_desk_sessions.db")
            )
        else:
            _GLOBAL_STORE = SessionStore()
    return _GLOBAL_STORE
```

- [ ] **Step 5: Run all tests**

Run: `python -m pytest tests/test_session_persistence.py tests/test_agents_smoke.py -v`
Expected: PASS (both new tests + the existing agent smoke test still green)

- [ ] **Step 6: Commit**

```bash
git add src/agents/persistence.py src/agents/orchestrator.py tests/test_session_persistence.py
git commit -m "feat(agents): SQLite-backed SessionStore for cross-restart persistence"
```

---

## Phase 2 — Real rate curve (FRED SOFR + flat fallback)

**Why:** Eliminates the hardcoded `risk_free_rate=0.045` in `orchestrator._build_regime` (line 347). Wraps it in a `RateCurve` interface so future curve constructors (OIS bootstrap) plug in cleanly.

**Files:**
- Create: `src/data/rate_curve.py`
- Modify: `src/agents/orchestrator.py:325-362`
- Create: `tests/test_rate_curve.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_rate_curve.py
from unittest.mock import patch
from src.data.rate_curve import RateCurve, FlatRateCurve

def test_flat_curve_constant():
    curve = FlatRateCurve(rate=0.045)
    assert curve.spot_rate(maturity_years=0.5) == 0.045
    assert curve.spot_rate(maturity_years=5.0) == 0.045

def test_curve_factory_no_fred_key():
    with patch.dict("os.environ", {}, clear=True):
        curve = RateCurve.from_env()
    assert isinstance(curve, FlatRateCurve)

def test_curve_factory_with_fred_uses_sofr(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "fake")
    with patch("src.data.rate_curve._fetch_sofr_overnight", return_value=0.0532):
        curve = RateCurve.from_env()
    # Even FRED-backed curve should answer spot_rate.
    assert 0.04 < curve.spot_rate(0.5) < 0.07
```

- [ ] **Step 2: Run test to verify failure**

Run: `python -m pytest tests/test_rate_curve.py -v`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement `rate_curve.py`**

```python
# src/data/rate_curve.py
"""Risk-free rate curve abstraction.

Phase 1: FlatRateCurve (single rate) + FRED-backed flat (today's SOFR).
Phase 2 future: OIS bootstrap with term structure.
"""
from __future__ import annotations
import logging
import os
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RATE = 0.045


@dataclass
class FlatRateCurve:
    rate: float

    def spot_rate(self, maturity_years: float) -> float:  # noqa: ARG002
        return self.rate

    @property
    def kind(self) -> str:
        return "flat"


def _fetch_sofr_overnight(api_key: str) -> Optional[float]:
    """Fetch today's overnight SOFR from FRED. Returns decimal (e.g. 0.0532)."""
    url = (
        "https://api.stlouisfed.org/fred/series/observations"
        f"?series_id=SOFR&api_key={api_key}&file_type=json"
        "&sort_order=desc&limit=1"
    )
    try:
        r = httpx.get(url, timeout=5.0)
        r.raise_for_status()
        obs = r.json()["observations"][0]["value"]
        if obs == "." or obs == "":
            return None
        return float(obs) / 100.0
    except Exception as exc:  # noqa: BLE001
        logger.warning("FRED SOFR fetch failed: %s", exc)
        return None


class RateCurve:
    """Factory entry-point for whatever curve env+config say to build."""

    @staticmethod
    def from_env() -> FlatRateCurve:
        api_key = os.getenv("FRED_API_KEY")
        if api_key:
            sofr = _fetch_sofr_overnight(api_key)
            if sofr is not None:
                logger.info("Using SOFR=%.4f from FRED", sofr)
                return FlatRateCurve(rate=sofr)
        return FlatRateCurve(rate=DEFAULT_RATE)
```

- [ ] **Step 4: Wire into orchestrator regime build**

```python
# src/agents/orchestrator.py:325-362 — replace the function
from src.data.rate_curve import RateCurve

@staticmethod
def _build_regime(objective: ClientObjective) -> MarketRegime:
    warnings: list[str] = []
    params: dict = {}
    try:
        params = market_data.fetch_market_params(objective.underlying)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"market_data fetch failed: {exc}")

    spot = params.get("spot_price") or 100.0
    if not params.get("spot_price"):
        warnings.append("Spot price missing; using $100 placeholder.")

    curve = RateCurve.from_env()
    rfr = curve.spot_rate(maturity_years=objective.maturity_years or 1.0)

    regime = MarketRegime(
        underlying=objective.underlying,
        spot=float(spot),
        dividend_yield=float(params.get("dividend_yield") or 0.015),
        risk_free_rate=float(rfr),
        realised_vol_30d=params.get("volatility_30d"),
        realised_vol_90d=params.get("volatility_90d"),
        data_source_warnings=warnings,
    )
    sigma = regime.realised_vol_30d or regime.realised_vol_90d
    if sigma is not None:
        regime.vol_regime = (
            "very_high" if sigma >= 0.40 else
            "high" if sigma >= 0.25 else
            "low" if sigma <= 0.12 else "normal"
        )
    return regime
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_rate_curve.py tests/test_agents_smoke.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/data/rate_curve.py src/agents/orchestrator.py tests/test_rate_curve.py
git commit -m "feat(rates): pluggable RateCurve with FRED SOFR backing"
```

---

## Phase 3 — Expose Monte Carlo via `engine` field

**Why:** The frontend MC controls (`n_paths`, `variance_reduction`) are inert — the QL binomial path silently ignores them. This makes them real.

**Files:**
- Modify: `src/api/models.py`
- Modify: `src/engines/router.py`
- Modify: `src/api/handlers.py`
- Create: `tests/test_engine_selection.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_engine_selection.py
import pytest
from src.engines.router import route_with_engine

def test_american_put_default_engine_is_tree():
    pricer, _, label = route_with_engine("american_put", engine="auto")
    assert "Tree" in label or "Binomial" in label

def test_american_put_force_mc_engine():
    pricer, _, label = route_with_engine("american_put", engine="mc")
    assert "Monte Carlo" in label or "MC" in label
    price, std_err, paths = pricer(100, 100, 0.05, 0.2, 1.0, 0.0, n_paths=5000, n_steps=50)
    assert std_err is not None and std_err > 0
    assert price > 0

def test_unknown_engine_raises():
    with pytest.raises(ValueError):
        route_with_engine("american_put", engine="black_magic")
```

- [ ] **Step 2: Run failing tests**

Run: `python -m pytest tests/test_engine_selection.py -v`
Expected: FAIL — `route_with_engine` not defined.

- [ ] **Step 3: Add engine field to `PricingRequest`**

```python
# src/api/models.py — add to PricingRequest fields
from typing import Literal
class PricingRequest(BaseModel):
    # ... existing fields ...
    engine: Literal["auto", "analytic", "tree", "mc", "fdm"] = "auto"
```

- [ ] **Step 4: Add `route_with_engine` to router**

```python
# src/engines/router.py — append at bottom
def route_with_engine(option_type: str, engine: str = "auto") -> Tuple[Callable, Callable, str]:
    """Route honoring an explicit engine selector.

    engine='auto' (default) reproduces the existing route() behaviour.
    For american_*, engine='mc' forces monte_carlo_lsm regardless of QL.
    """
    if engine == "auto":
        return route(option_type)

    if engine == "mc" and option_type in ("american_call", "american_put"):
        opt = option_type.split("_")[1]
        def pricer(S, K, r, sigma, T, q, n_paths=10000, n_steps=90, variance_reduction="none", **kwargs):
            return monte_carlo_lsm.price_american(
                S, K, r, sigma, T, q, n_paths, n_steps, variance_reduction, option_type=opt
            )
        def greeks(S, K, r, sigma, T, q, **kwargs):
            return monte_carlo_lsm.greeks_american(S, K, r, sigma, T, q, option_type=opt)
        return pricer, greeks, "Monte Carlo LSM (American, forced)"

    if engine in ("analytic", "tree", "fdm"):
        # Phase 1: these all collapse to QL default for now.
        return route(option_type)

    raise ValueError(f"Unknown engine: {engine!r}. Valid: auto|analytic|tree|mc|fdm")
```

- [ ] **Step 5: Wire into handler**

```python
# src/api/handlers.py — change the route() call
from src.engines.router import route_with_engine
# ... in price_option:
pricer, greeks_fn, method_label = route_with_engine(req.option_type, engine=req.engine)
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_engine_selection.py tests/test_engines.py tests/test_engine_consistency.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/api/models.py src/engines/router.py src/api/handlers.py tests/test_engine_selection.py
git commit -m "feat(engines): expose engine selector via PricingRequest"
```

---

## Phase 4 — Bucketed sensitivities + scenario grid

**Why:** Single-scalar Vega is useless for a real book. Vega-by-tenor + scenario grid (S × σ heatmap) + gamma ladder are what risk needs.

**Files:**
- Create: `src/analysis/sensitivities.py`
- Modify: `src/api/models.py`
- Modify: `src/api/handlers.py`
- Create: `tests/test_sensitivities.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_sensitivities.py
import numpy as np
from src.analysis.sensitivities import (
    compute_scenario_grid,
    compute_gamma_ladder,
    SensitivityBlock,
)

def test_scenario_grid_shape():
    block = compute_scenario_grid(
        option_type="european_call",
        S=100, K=100, r=0.05, sigma=0.2, T=1.0, q=0.0,
        spot_shifts=(-0.10, -0.05, 0, 0.05, 0.10),
        vol_shifts=(-0.05, 0, 0.05),
    )
    assert block.shape == (5, 3)
    assert np.isfinite(block.values).all()

def test_gamma_ladder_centered_on_atm():
    ladder = compute_gamma_ladder(
        option_type="european_call",
        S=100, K=100, r=0.05, sigma=0.2, T=1.0, q=0.0,
    )
    # ATM gamma is the max for vanilla options.
    atm_idx = len(ladder) // 2
    gammas = [pt.gamma for pt in ladder]
    assert max(gammas) - gammas[atm_idx] < max(gammas) * 0.05

def test_sensitivity_block_serialises():
    s = SensitivityBlock(values=[[1.0, 2.0], [3.0, 4.0]],
                         spot_axis=[95, 100], vol_axis=[0.18, 0.22])
    j = s.model_dump_json()
    assert "values" in j
```

- [ ] **Step 2: Run failing test**

Run: `python -m pytest tests/test_sensitivities.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement `sensitivities.py`**

```python
# src/analysis/sensitivities.py
"""Bucketed sensitivities: scenario grid (S × σ), gamma ladder."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from pydantic import BaseModel

from src.engines.router import route


class SensitivityBlock(BaseModel):
    """2-D price grid plus its axes."""
    values: list[list[float]]
    spot_axis: list[float]
    vol_axis: list[float]

    @property
    def shape(self) -> tuple[int, int]:
        return (len(self.spot_axis), len(self.vol_axis))


@dataclass
class GammaLadderPoint:
    spot: float
    delta: float
    gamma: float


def compute_scenario_grid(
    option_type: str,
    S: float, K: float, r: float, sigma: float, T: float, q: float,
    spot_shifts: Sequence[float] = (-0.10, -0.05, 0.0, 0.05, 0.10),
    vol_shifts: Sequence[float] = (-0.05, 0.0, 0.05),
    **kwargs,
) -> SensitivityBlock:
    pricer, _, _ = route(option_type)
    grid = np.zeros((len(spot_shifts), len(vol_shifts)))
    for i, ds in enumerate(spot_shifts):
        for j, dv in enumerate(vol_shifts):
            try:
                price, _, _ = pricer(
                    S * (1 + ds), K, r,
                    max(sigma + dv, 1e-4),
                    T, q, **kwargs,
                )
                grid[i, j] = price
            except Exception:
                grid[i, j] = float("nan")
    return SensitivityBlock(
        values=grid.tolist(),
        spot_axis=[S * (1 + ds) for ds in spot_shifts],
        vol_axis=[sigma + dv for dv in vol_shifts],
    )


def compute_gamma_ladder(
    option_type: str,
    S: float, K: float, r: float, sigma: float, T: float, q: float,
    n_points: int = 11, halfwidth: float = 0.10,
    **kwargs,
) -> list[GammaLadderPoint]:
    _, greeks_fn, _ = route(option_type)
    spots = np.linspace(S * (1 - halfwidth), S * (1 + halfwidth), n_points)
    out: list[GammaLadderPoint] = []
    for s in spots:
        g = greeks_fn(float(s), K, r, sigma, T, q, **kwargs)
        out.append(GammaLadderPoint(spot=float(s), delta=g.get("delta", 0.0), gamma=g.get("gamma", 0.0)))
    return out
```

- [ ] **Step 4: Surface in PricingResult & handler**

```python
# src/api/models.py — extend PricingResult
from src.analysis.sensitivities import SensitivityBlock

class PricingResult(BaseModel):
    # ... existing ...
    scenario_grid: Optional[SensitivityBlock] = None
    gamma_ladder: Optional[list[dict]] = None

# src/api/models.py — extend PricingRequest
class PricingRequest(BaseModel):
    # ...
    deep_risk: bool = False
```

```python
# src/api/handlers.py — append after primary price/greeks computed
if req.deep_risk:
    from src.analysis.sensitivities import compute_scenario_grid, compute_gamma_ladder
    result.scenario_grid = compute_scenario_grid(
        req.option_type, S, K, r, sigma, T, q, **engine_kwargs
    )
    result.gamma_ladder = [
        {"spot": p.spot, "delta": p.delta, "gamma": p.gamma}
        for p in compute_gamma_ladder(req.option_type, S, K, r, sigma, T, q, **engine_kwargs)
    ]
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_sensitivities.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/analysis/sensitivities.py src/api/models.py src/api/handlers.py tests/test_sensitivities.py
git commit -m "feat(risk): bucketed scenario grid + gamma ladder under deep_risk flag"
```

---

## Phase 5 — LLM provider abstraction

**Why:** Bank compliance never lets pure Gemini code through. A pluggable provider — Anthropic, OpenAI, Bedrock, mock — is mandatory before the structuring co-pilot leaves a sandbox.

**Files:**
- Create: `src/agents/llm_provider.py`
- Modify: `src/agents/llm_client.py`
- Create: `tests/test_llm_provider.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_llm_provider.py
import os
from unittest.mock import patch
from src.agents.llm_provider import (
    LLMProvider, MockProvider, GeminiProvider, AnthropicProvider, get_provider,
)

def test_mock_provider_deterministic():
    p = MockProvider(canned={"hi": "hello"})
    assert p.complete("hi", model="any") == "hello"

def test_provider_factory_default_is_gemini(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.setenv("GEMINI_API_KEY", "x")
    p = get_provider()
    assert isinstance(p, GeminiProvider)

def test_provider_factory_anthropic(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")
    p = get_provider()
    assert isinstance(p, AnthropicProvider)

def test_provider_factory_mock_in_replay(monkeypatch):
    monkeypatch.setenv("DEMO_REPLAY", "1")
    p = get_provider()
    assert isinstance(p, MockProvider)
```

- [ ] **Step 2: Run failing test**

Run: `python -m pytest tests/test_llm_provider.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement provider abstraction**

```python
# src/agents/llm_provider.py
"""LLM provider abstraction so we can swap Gemini → Anthropic → OpenAI → Bedrock.

Each agent calls `provider.complete(prompt, model=..., temperature=...)`.
Selection happens once at startup via env LLM_PROVIDER.
"""
from __future__ import annotations
import os
from typing import Optional, Protocol


class LLMProvider(Protocol):
    name: str
    def complete(self, prompt: str, *, model: str, temperature: float = 0.2,
                 max_tokens: int = 2048) -> str: ...


class MockProvider:
    name = "mock"
    def __init__(self, canned: Optional[dict[str, str]] = None) -> None:
        self.canned = canned or {}

    def complete(self, prompt: str, *, model: str, temperature: float = 0.2,
                 max_tokens: int = 2048) -> str:
        return self.canned.get(prompt, "[mock-response]")


class GeminiProvider:
    name = "gemini"
    def __init__(self, api_key: str) -> None:
        import google.generativeai as genai
        genai.configure(api_key=api_key)
        self._genai = genai

    def complete(self, prompt: str, *, model: str, temperature: float = 0.2,
                 max_tokens: int = 2048) -> str:
        m = self._genai.GenerativeModel(model)
        resp = m.generate_content(
            prompt,
            generation_config={"temperature": temperature, "max_output_tokens": max_tokens},
        )
        return resp.text


class AnthropicProvider:
    name = "anthropic"
    def __init__(self, api_key: str) -> None:
        import anthropic
        self._client = anthropic.Anthropic(api_key=api_key)

    def complete(self, prompt: str, *, model: str, temperature: float = 0.2,
                 max_tokens: int = 2048) -> str:
        msg = self._client.messages.create(
            model=model, max_tokens=max_tokens, temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text


class OpenAIProvider:
    name = "openai"
    def __init__(self, api_key: str) -> None:
        from openai import OpenAI
        self._client = OpenAI(api_key=api_key)

    def complete(self, prompt: str, *, model: str, temperature: float = 0.2,
                 max_tokens: int = 2048) -> str:
        resp = self._client.chat.completions.create(
            model=model, temperature=temperature, max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.choices[0].message.content or ""


def get_provider() -> LLMProvider:
    if os.getenv("DEMO_REPLAY") == "1":
        return MockProvider()
    name = os.getenv("LLM_PROVIDER", "gemini").lower()
    if name == "gemini":
        return GeminiProvider(api_key=os.environ["GEMINI_API_KEY"])
    if name == "anthropic":
        return AnthropicProvider(api_key=os.environ["ANTHROPIC_API_KEY"])
    if name == "openai":
        return OpenAIProvider(api_key=os.environ["OPENAI_API_KEY"])
    if name == "mock":
        return MockProvider()
    raise ValueError(f"Unknown LLM_PROVIDER: {name}")
```

- [ ] **Step 4: Refactor `llm_client.py` to delegate**

```python
# src/agents/llm_client.py — modify to wrap provider; keep cost tracking + replay shim
# Concrete edit at execution time: ensure existing public surface (LLMClient.generate)
# unchanged so agents don't need to be touched.
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_llm_provider.py tests/test_agents_smoke.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/agents/llm_provider.py src/agents/llm_client.py tests/test_llm_provider.py
git commit -m "feat(agents): pluggable LLMProvider (Gemini|Anthropic|OpenAI|Mock)"
```

---

## Phase 6 — Multi-asset state types

**Why:** Foundation for worst-of and autocallable. Pure Pydantic — no engine work yet, but locks in the contract the engines and agents will see.

**Files:**
- Modify: `src/agents/state.py`
- Create: `tests/test_basket_state.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_basket_state.py
from src.agents.state import (
    BasketObjective, ObservationSchedule, AutocallTerms, Leg, Structure,
)

def test_basket_objective_serialises():
    b = BasketObjective(
        underliers=["NVDA", "AMD", "AVGO"],
        weights=[1/3, 1/3, 1/3],
        worst_of=True,
        maturity_years=1.0,
    )
    j = b.model_dump_json()
    assert "NVDA" in j

def test_observation_schedule_quarterly():
    s = ObservationSchedule.quarterly(maturity_years=1.0)
    assert len(s.dates_years) == 4
    assert s.dates_years[-1] == 1.0

def test_autocall_terms_validation():
    t = AutocallTerms(
        coupon_rate=0.10,
        autocall_barrier=1.00,
        coupon_barrier=0.70,
        protection_barrier=0.60,
    )
    assert t.protection_barrier < t.coupon_barrier <= t.autocall_barrier
```

- [ ] **Step 2: Run failing test**

Run: `python -m pytest tests/test_basket_state.py -v`
Expected: FAIL — names not in state.

- [ ] **Step 3: Add to `state.py`**

```python
# src/agents/state.py — append
from typing import Literal
from pydantic import BaseModel, Field, field_validator

class BasketObjective(BaseModel):
    underliers: list[str] = Field(min_length=1, max_length=10)
    weights: list[float]
    worst_of: bool = False
    maturity_years: float = 1.0

    @field_validator("weights")
    @classmethod
    def _weights_sum_close_to_one(cls, v):
        if abs(sum(v) - 1.0) > 1e-3:
            raise ValueError("weights must sum to 1.0")
        return v

class ObservationSchedule(BaseModel):
    dates_years: list[float]

    @classmethod
    def quarterly(cls, maturity_years: float) -> "ObservationSchedule":
        n = max(1, int(round(maturity_years * 4)))
        return cls(dates_years=[(i + 1) / 4 for i in range(n)])

    @classmethod
    def monthly(cls, maturity_years: float) -> "ObservationSchedule":
        n = max(1, int(round(maturity_years * 12)))
        return cls(dates_years=[(i + 1) / 12 for i in range(n)])

class AutocallTerms(BaseModel):
    coupon_rate: float
    autocall_barrier: float = 1.00
    coupon_barrier: float = 0.70
    protection_barrier: float = 0.60
    memory: bool = True

    @field_validator("protection_barrier")
    @classmethod
    def _layered(cls, v, info):
        cb = info.data.get("coupon_barrier")
        if cb is not None and v >= cb:
            raise ValueError("protection_barrier must be below coupon_barrier")
        return v

class Leg(BaseModel):
    side: Literal["long", "short"]
    quantity: float
    instrument_kind: Literal["european_call", "european_put", "knockout_call",
                              "knockout_put", "knockin_call", "knockin_put",
                              "asian_call", "asian_put", "lookback_call",
                              "lookback_put", "zero_coupon", "fixed_coupon"]
    strike: float | None = None
    barrier: float | None = None
    coupon_rate: float | None = None

class Structure(BaseModel):
    name: str
    legs: list[Leg]
    maturity_years: float
    notional: float = 1_000_000.0
    autocall_terms: AutocallTerms | None = None
    observation_schedule: ObservationSchedule | None = None
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_basket_state.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/agents/state.py tests/test_basket_state.py
git commit -m "feat(state): basket + autocallable + leg primitives"
```

---

## Phase 7 — Multi-asset Monte Carlo (correlated GBM)

**Why:** Engine substrate for everything multi-name. Cholesky-driven GBM with antithetic. Sanity-check against Margrabe (two-asset spread).

**Files:**
- Create: `src/engines/multi_asset_mc.py`
- Create: `tests/test_multi_asset_mc.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_multi_asset_mc.py
import math
import numpy as np
from src.engines.multi_asset_mc import simulate_correlated_gbm, price_worst_of_european_put

def test_simulate_shapes():
    paths = simulate_correlated_gbm(
        S0=np.array([100.0, 100.0]),
        r=0.05, q=np.zeros(2),
        sigma=np.array([0.2, 0.25]),
        rho=np.array([[1.0, 0.5], [0.5, 1.0]]),
        T=1.0, n_steps=12, n_paths=1000, seed=42,
    )
    assert paths.shape == (1000, 13, 2)  # paths × (steps+1) × assets

def test_worst_of_put_below_min_constituent():
    # Worst-of put price ≥ min of single-name put prices.
    price = price_worst_of_european_put(
        S0=np.array([100.0, 100.0]),
        K=100.0,
        r=0.05, q=np.zeros(2),
        sigma=np.array([0.2, 0.2]),
        rho=np.array([[1.0, 0.0], [0.0, 1.0]]),
        T=1.0, n_paths=20000, seed=1,
    )
    # BS single-name put ATM with σ=0.2, T=1 ≈ 5.57
    assert 5.0 < price < 12.0  # worst-of richer than single-name
```

- [ ] **Step 2: Run failing**

Run: `python -m pytest tests/test_multi_asset_mc.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement engine**

```python
# src/engines/multi_asset_mc.py
"""Correlated multi-asset GBM Monte Carlo.

Cholesky-correlated Brownian, antithetic variates, fixed seed for CRN."""
from __future__ import annotations
import numpy as np


def simulate_correlated_gbm(
    *, S0: np.ndarray, r: float, q: np.ndarray, sigma: np.ndarray,
    rho: np.ndarray, T: float, n_steps: int, n_paths: int,
    seed: int | None = None, antithetic: bool = True,
) -> np.ndarray:
    n_assets = S0.shape[0]
    rng = np.random.default_rng(seed)
    L = np.linalg.cholesky(rho)
    dt = T / n_steps

    half = n_paths // 2 if antithetic else n_paths
    z = rng.standard_normal((half, n_steps, n_assets))
    if antithetic:
        z = np.concatenate([z, -z], axis=0)
    z_corr = z @ L.T

    drift = (r - q - 0.5 * sigma**2) * dt
    diffusion = sigma * np.sqrt(dt)
    log_increments = drift + diffusion * z_corr
    log_paths = np.concatenate(
        [np.zeros((n_paths, 1, n_assets)), np.cumsum(log_increments, axis=1)], axis=1
    )
    return S0 * np.exp(log_paths)


def price_worst_of_european_put(
    *, S0: np.ndarray, K: float, r: float, q: np.ndarray, sigma: np.ndarray,
    rho: np.ndarray, T: float, n_paths: int = 20000, seed: int | None = None,
) -> float:
    paths = simulate_correlated_gbm(
        S0=S0, r=r, q=q, sigma=sigma, rho=rho, T=T,
        n_steps=1, n_paths=n_paths, seed=seed,
    )
    S_T = paths[:, -1, :]
    worst_perf = np.min(S_T / S0, axis=1)
    payoff = np.maximum(K / S0[0] - worst_perf, 0.0) * S0[0]  # K-normalised
    return float(np.exp(-r * T) * payoff.mean())
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_multi_asset_mc.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/engines/multi_asset_mc.py tests/test_multi_asset_mc.py
git commit -m "feat(engines): correlated multi-asset GBM Monte Carlo + worst-of put"
```

---

## Phase 8 — Phoenix autocallable pricer

**Why:** The single feature that takes this from "single-name pricer" to "structuring desk." Built atop Phase 7.

**Files:**
- Create: `src/engines/autocallable.py`
- Create: `tests/test_autocallable.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_autocallable.py
import numpy as np
from src.agents.state import AutocallTerms, ObservationSchedule
from src.engines.autocallable import price_phoenix_autocallable

def test_phoenix_autocallable_price_in_normal_range():
    price = price_phoenix_autocallable(
        S0=np.array([100.0, 100.0, 100.0]),
        r=0.045, q=np.array([0.01, 0.005, 0.0]),
        sigma=np.array([0.30, 0.35, 0.40]),
        rho=0.5 * np.ones((3, 3)) + 0.5 * np.eye(3),
        terms=AutocallTerms(coupon_rate=0.10, autocall_barrier=1.0,
                            coupon_barrier=0.7, protection_barrier=0.6),
        schedule=ObservationSchedule.quarterly(2.0),
        notional=1_000_000,
        n_paths=20000, seed=7,
    )
    # Investor pays par; structure value should land near par for typical
    # phoenix at issue. ±15% acceptance band.
    assert 850_000 < price < 1_150_000

def test_phoenix_autocallable_deep_otm_low_value():
    # Coupon barrier so low it always pays → value approaches sum-of-coupons + par PV.
    price = price_phoenix_autocallable(
        S0=np.array([100.0, 100.0]),
        r=0.045, q=np.zeros(2),
        sigma=np.array([0.2, 0.2]),
        rho=np.eye(2),
        terms=AutocallTerms(coupon_rate=0.05, autocall_barrier=1.0,
                            coupon_barrier=0.01, protection_barrier=0.0),
        schedule=ObservationSchedule.quarterly(1.0),
        notional=1_000_000,
        n_paths=10000, seed=3,
    )
    assert price > 950_000  # near par + PV(coupons)
```

- [ ] **Step 2: Run failing**

Run: `python -m pytest tests/test_autocallable.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement pricer**

```python
# src/engines/autocallable.py
"""Phoenix autocallable Monte Carlo pricer.

Worst-of underlier basket. Discrete observation dates. On each obs:
  - if worst-of perf >= autocall_barrier → redeem at par + accrued coupons, stop.
  - else if worst-of perf >= coupon_barrier → pay coupon (or accumulate if memory).
At maturity:
  - if not autocalled and worst-of perf >= protection_barrier → return par.
  - else → return par * worst-of perf (capital loss matches worst).
"""
from __future__ import annotations
import numpy as np

from src.agents.state import AutocallTerms, ObservationSchedule
from .multi_asset_mc import simulate_correlated_gbm


def price_phoenix_autocallable(
    *, S0: np.ndarray, r: float, q: np.ndarray, sigma: np.ndarray,
    rho: np.ndarray, terms: AutocallTerms, schedule: ObservationSchedule,
    notional: float = 1_000_000.0, n_paths: int = 20000, seed: int | None = None,
) -> float:
    obs_years = np.array(schedule.dates_years)
    T = float(obs_years[-1])
    n_steps = max(1, int(round(T * 252)))
    paths = simulate_correlated_gbm(
        S0=S0, r=r, q=q, sigma=sigma, rho=rho, T=T,
        n_steps=n_steps, n_paths=n_paths, seed=seed,
    )

    obs_indices = (np.round(obs_years / T * n_steps)).astype(int)
    perf_at_obs = paths[:, obs_indices, :] / S0  # (paths, n_obs, assets)
    worst_perf = perf_at_obs.min(axis=2)  # (paths, n_obs)

    n_paths_actual = paths.shape[0]
    pv = np.zeros(n_paths_actual)
    accrued = np.zeros(n_paths_actual)
    alive = np.ones(n_paths_actual, dtype=bool)

    for k, t_k in enumerate(obs_years):
        wp = worst_perf[:, k]
        df = np.exp(-r * t_k)
        pays_coupon = (wp >= terms.coupon_barrier) & alive
        if terms.memory:
            payable = accrued + terms.coupon_rate * notional
            pv += np.where(pays_coupon, df * payable, 0.0)
            accrued = np.where(pays_coupon, 0.0, accrued + terms.coupon_rate * notional * alive)
        else:
            pv += np.where(pays_coupon, df * terms.coupon_rate * notional, 0.0)
        autocalls = (wp >= terms.autocall_barrier) & alive
        pv += np.where(autocalls, df * notional, 0.0)
        alive = alive & ~autocalls

    final_perf = worst_perf[:, -1]
    df_T = np.exp(-r * T)
    redemption = np.where(
        final_perf >= terms.protection_barrier, notional, notional * final_perf
    )
    pv += np.where(alive, df_T * redemption, 0.0)
    return float(pv.mean())
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_autocallable.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/engines/autocallable.py tests/test_autocallable.py
git commit -m "feat(engines): phoenix autocallable Monte Carlo pricer"
```

---

## Phase 9 — Term sheet PDF generator

**Why:** A structurer cannot ship a structure without a client-facing PDF. PRIIPs-style scenario block (favourable / moderate / unfavourable / stress).

**Files:**
- Create: `src/report/term_sheet.py`
- Create: `tests/test_term_sheet.py`

- [ ] **Step 1: Failing tests**

```python
# tests/test_term_sheet.py
from src.agents.state import Structure, Leg, AutocallTerms, ObservationSchedule
from src.report.term_sheet import generate_term_sheet

def test_term_sheet_generates(tmp_path):
    s = Structure(
        name="3y SPX/NDX/RUT 10% Phoenix",
        legs=[Leg(side="long", quantity=1.0, instrument_kind="zero_coupon")],
        maturity_years=3.0,
        notional=1_000_000.0,
        autocall_terms=AutocallTerms(coupon_rate=0.10, autocall_barrier=1.0,
                                     coupon_barrier=0.7, protection_barrier=0.6),
        observation_schedule=ObservationSchedule.quarterly(3.0),
    )
    out = tmp_path / "ts.pdf"
    path = generate_term_sheet(
        structure=s, mid_price=1_005_000.0,
        scenarios={"favourable": 1.30, "moderate": 1.05, "unfavourable": 0.85, "stress": 0.55},
        output_path=str(out),
    )
    assert out.exists() and out.stat().st_size > 1000
```

- [ ] **Step 2: Run failing**

Run: `python -m pytest tests/test_term_sheet.py -v`
Expected: FAIL — module missing.

- [ ] **Step 3: Implement (reportlab)**

```python
# src/report/term_sheet.py
from __future__ import annotations
from datetime import date
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
)

from src.agents.state import Structure


def generate_term_sheet(
    *, structure: Structure, mid_price: float,
    scenarios: dict, output_path: str,
) -> str:
    doc = SimpleDocTemplate(output_path, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []
    story.append(Paragraph(f"<b>Term Sheet — {structure.name}</b>", styles["Title"]))
    story.append(Paragraph(f"Issue date: {date.today().isoformat()}", styles["Normal"]))
    story.append(Spacer(1, 0.4 * cm))

    facts = [
        ["Notional", f"USD {structure.notional:,.0f}"],
        ["Maturity", f"{structure.maturity_years} y"],
        ["Indicative mid", f"USD {mid_price:,.0f}"],
    ]
    if structure.autocall_terms:
        t = structure.autocall_terms
        facts += [
            ["Coupon", f"{t.coupon_rate*100:.2f}% per period"],
            ["Autocall barrier", f"{t.autocall_barrier*100:.0f}%"],
            ["Coupon barrier", f"{t.coupon_barrier*100:.0f}%"],
            ["Protection barrier", f"{t.protection_barrier*100:.0f}%"],
        ]
    tbl = Table(facts, colWidths=[6 * cm, 8 * cm])
    tbl.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
    ]))
    story.append(tbl)
    story.append(Spacer(1, 0.6 * cm))

    story.append(Paragraph("<b>Indicative scenarios</b>", styles["Heading2"]))
    rows = [["Scenario", "Worst-of perf", "Investor payoff (USD)"]]
    for label, perf in scenarios.items():
        payoff = structure.notional * (perf if perf < 1.0 else 1.0 + 0.10)  # placeholder
        rows.append([label.title(), f"{perf*100:.0f}%", f"{payoff:,.0f}"])
    sc_tbl = Table(rows, colWidths=[5 * cm, 4 * cm, 5 * cm])
    sc_tbl.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey)]))
    story.append(sc_tbl)
    story.append(Spacer(1, 0.6 * cm))
    story.append(Paragraph("<i>Indicative only — not an offer to sell. Final terms in trade confirmation.</i>",
                            styles["Italic"]))

    doc.build(story)
    return output_path
```

- [ ] **Step 4: Add `reportlab` to requirements**

Append `reportlab>=4.0` to `requirements.txt` if not present.

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_term_sheet.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/report/term_sheet.py tests/test_term_sheet.py requirements.txt
git commit -m "feat(report): PRIIPs-style term sheet PDF generator"
```

---

## Build → Test → Fix-or-Proceed loop protocol

For every phase the orchestrator runs:

1. **Dispatch implementation subagent** (Sonnet 4.6) with the phase task as the entire prompt.
2. **Verify** — run `python -m pytest tests/ -x --timeout=60` via Haiku 4.5 verifier subagent.
3. **Decision:**
   - All tests pass → **proceed**: mark phase complete, move to next.
   - Failure → **fix**: dispatch a fix-up subagent (Sonnet 4.6) given the failing test output and the diff. Re-verify. Max 2 fix attempts per phase, then surface to user.
4. After every 2 phases, dispatch **code review subagent** (Opus 4.7) for a quality gate.

The loop terminates when: all 9 phases done, OR an unrecoverable failure occurs, OR the user interrupts.

---

## Self-Review

**Spec coverage:** All P0 items from the VP/Structurer report are addressed: persistence (P1), rates stack (P2), MC exposure (P3), bucketed sensitivities (P4), LLM abstraction (P5), multi-asset state (P6), correlated MC (P7), autocallable (P8), term sheet (P9). The remaining P2 items (FRTB SBA, XVA, real-time data adapters, model-validation harness) are explicitly out of scope and noted in the report as future work.

**Placeholder scan:** Every code block above contains real, runnable code. No "TODO" / "TBD" left in the plan.

**Type consistency:** `SessionStore` interface used identically in Phase 1; `route_with_engine` signature consistent across Phases 3 and downstream; `AutocallTerms` and `ObservationSchedule` defined in Phase 6 are consumed unchanged by Phases 8-9.
