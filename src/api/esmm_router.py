"""FastAPI router for the eSMM lab.

Endpoints:
- POST /api/esmm/backtest             — synthetic backtest, return TCA + path
- POST /api/esmm/backtest/snapshots   — backtest a raw snapshot list (any source)
- POST /api/esmm/backtest/live        — backtest via a registered DataAdapter
- POST /api/esmm/quote                — single-shot quote
- POST /api/esmm/crb/internalise      — single-symbol CRB
- POST /api/esmm/crb/internalise-book — multi-symbol CRB
- GET  /api/esmm/synthetic-book       — preview a synthetic snapshot path
- GET  /api/esmm/adapters             — list available data adapters
- GET  /api/esmm/backtests            — history (when persistence enabled)
- GET  /api/esmm/backtests/{id}       — replay a stored run
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from src.esmm import persistence
from src.esmm.adapters import (
    AlpacaAdapter,
    DataAdapter,
    SyntheticAdapter,
    TradierAdapter,
    YFinanceAdapter,
)
from src.esmm.backtest import run_backtest
from src.esmm.crb import CentralRiskBook
from src.esmm.inventory import InventoryBook
from src.esmm.quote_engine import QuoteEngine
from src.esmm.schemas import (
    CRBBookFlow,
    CRBBookResult,
    CRBInternalisationResult,
    Fill,
    MarketMakingConfig,
    OrderBookSnapshot,
    Position,
    Quote,
    Side,
    TCABreakdown,
)
from src.esmm.synthetic import generate_order_book_path

# Registered adapters available to /backtest/live. Keyed by adapter.name.
_ADAPTER_REGISTRY: dict[str, type] = {
    "synthetic": SyntheticAdapter,
    "yfinance": YFinanceAdapter,
    "alpaca": AlpacaAdapter,
    "tradier": TradierAdapter,
}

router = APIRouter(prefix="/api/esmm", tags=["esmm"])


class BacktestRequest(BaseModel):
    config: MarketMakingConfig
    n_snaps: int = Field(default=300, ge=10, le=10000)
    start_price: float = 500.0
    sigma_per_step: float = 0.0005
    base_spread_bps: float = 5.0
    seed: int = 42


class BacktestResponse(BaseModel):
    n_quotes: int
    n_fills: int
    final_inventory: float
    final_mid: float
    realised_pnl: float
    unrealised_pnl: float
    total_pnl: float
    tca: TCABreakdown
    mid_path_sample: list[tuple[float, float]]  # downsampled to ≤ 100 points for UI
    inventory_path_sample: list[tuple[float, float]]
    saved_id: Optional[str] = None  # set when ESMM_PERSIST=1


class CRBBookRequest(BaseModel):
    snapshots: list[OrderBookSnapshot]  # one per symbol
    flows: list[CRBBookFlow]
    internalisation_cap_pct: float = Field(default=1.0, ge=0.0, le=1.0)


class BacktestRecordView(BaseModel):
    id: str
    created_ts: float
    symbol: str
    n_quotes: int
    n_fills: int
    total_pnl: float
    final_inventory: float
    config: dict
    tca: dict


class QuoteRequest(BaseModel):
    snapshot: OrderBookSnapshot
    config: MarketMakingConfig
    seed_position: Optional[Position] = None
    adverse_selection_bps: float = 0.0


class CRBRequest(BaseModel):
    snapshot: OrderBookSnapshot
    incoming_buys: float
    incoming_sells: float
    internalisation_cap_pct: float = Field(default=1.0, ge=0.0, le=1.0)


class SyntheticBookRequest(BaseModel):
    n_snaps: int = Field(default=200, ge=10, le=2000)
    symbol: str = "SPY"
    start_price: float = 500.0
    sigma_per_step: float = 0.0005
    base_spread_bps: float = 5.0
    seed: int = 42


@router.post("/backtest", response_model=BacktestResponse)
def backtest(request: BacktestRequest) -> BacktestResponse:
    snaps = generate_order_book_path(
        symbol=request.config.symbol,
        n_snaps=request.n_snaps,
        start_price=request.start_price,
        sigma_per_step=request.sigma_per_step,
        base_spread_bps=request.base_spread_bps,
        seed=request.seed,
    )
    result = run_backtest(snaps, request.config)
    if result.tca is None:
        raise HTTPException(status_code=500, detail="TCA was not produced")

    # Downsample mid_path + inventory_path to ≤ 100 points for transport
    step = max(1, len(result.mid_path) // 100)
    mid_sample = result.mid_path[::step]
    inv_sample = result.inventory_path[::step]

    saved_id = persistence.save_backtest(
        symbol=request.config.symbol,
        config=request.config.model_dump(),
        tca=result.tca,
        n_quotes=result.n_quotes,
        n_fills=result.n_fills,
        total_pnl=result.total_pnl,
        final_inventory=result.final_inventory,
    )

    return BacktestResponse(
        n_quotes=result.n_quotes,
        n_fills=result.n_fills,
        final_inventory=result.final_inventory,
        final_mid=result.final_mid,
        realised_pnl=result.realised_pnl,
        unrealised_pnl=result.unrealised_pnl,
        total_pnl=result.total_pnl,
        tca=TCABreakdown(**result.tca),
        mid_path_sample=mid_sample,
        inventory_path_sample=inv_sample,
        saved_id=saved_id,
    )


@router.post("/crb/internalise-book", response_model=CRBBookResult)
def crb_internalise_book(request: CRBBookRequest) -> CRBBookResult:
    crb = CentralRiskBook(internalisation_cap_pct=request.internalisation_cap_pct)
    snaps_by_symbol = {s.symbol: s for s in request.snapshots}
    return crb.internalise_book(snaps_by_symbol, request.flows)


@router.get("/backtests", response_model=list[BacktestRecordView])
def list_backtests(limit: int = 50) -> list[BacktestRecordView]:
    if limit < 1 or limit > 500:
        raise HTTPException(status_code=422, detail="limit must be in [1, 500]")
    records = persistence.list_backtests(limit=limit)
    return [BacktestRecordView(**r.__dict__) for r in records]


@router.get("/backtests/{record_id}", response_model=BacktestRecordView)
def get_backtest(record_id: str) -> BacktestRecordView:
    record = persistence.get_backtest(record_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"backtest {record_id} not found")
    return BacktestRecordView(**record.__dict__)


@router.post("/quote", response_model=Quote)
def quote(request: QuoteRequest) -> Quote:
    if request.snapshot.symbol != request.config.symbol:
        raise HTTPException(
            status_code=400,
            detail=f"snapshot.symbol {request.snapshot.symbol} != config.symbol {request.config.symbol}",
        )
    inv = InventoryBook()
    if request.seed_position is not None and request.seed_position.quantity != 0:
        seed_fill = Fill(
            ts=request.snapshot.ts,
            symbol=request.seed_position.symbol,
            side=Side.BUY if request.seed_position.quantity > 0 else Side.SELL,
            price=request.seed_position.avg_cost,
            size=abs(request.seed_position.quantity),
            fair_value_at_fill=request.seed_position.avg_cost,
        )
        inv.apply_fill(seed_fill)
    engine = QuoteEngine(request.config)
    return engine.quote(
        request.snapshot,
        inv,
        adverse_selection_bps=request.adverse_selection_bps,
    )


@router.post("/crb/internalise", response_model=CRBInternalisationResult)
def crb_internalise(request: CRBRequest) -> CRBInternalisationResult:
    crb = CentralRiskBook(internalisation_cap_pct=request.internalisation_cap_pct)
    return crb.internalise(
        request.snapshot,
        incoming_buys=request.incoming_buys,
        incoming_sells=request.incoming_sells,
    )


@router.post("/synthetic-book", response_model=list[OrderBookSnapshot])
def synthetic_book(request: SyntheticBookRequest) -> list[OrderBookSnapshot]:
    return generate_order_book_path(
        symbol=request.symbol,
        n_snaps=request.n_snaps,
        start_price=request.start_price,
        sigma_per_step=request.sigma_per_step,
        base_spread_bps=request.base_spread_bps,
        seed=request.seed,
    )


# ---------------------------------------------------------------------------
# Real-data endpoints
# ---------------------------------------------------------------------------


class SnapshotsBacktestRequest(BaseModel):
    """Backtest from a caller-supplied snapshot list (any data source)."""
    config: MarketMakingConfig
    snapshots: list[OrderBookSnapshot] = Field(..., min_length=2)


class LiveBacktestRequest(BaseModel):
    """Backtest a real-data window via a registered DataAdapter."""
    config: MarketMakingConfig
    adapter: str = Field(..., description="Adapter name; see GET /api/esmm/adapters")
    start: datetime
    end: datetime
    adapter_kwargs: dict = Field(default_factory=dict)


class AdapterInfo(BaseModel):
    """Metadata for one registered adapter."""
    name: str
    docstring: str


def _make_backtest_response(
    request_config: MarketMakingConfig, result, source: str
) -> "BacktestResponse":
    """Shared response builder for the snapshot + live endpoints."""
    if result.tca is None:
        raise HTTPException(status_code=500, detail="TCA was not produced")

    step = max(1, len(result.mid_path) // 100)
    mid_sample = result.mid_path[::step]
    inv_sample = result.inventory_path[::step]

    saved_id = persistence.save_backtest(
        symbol=request_config.symbol,
        config={**request_config.model_dump(), "_source": source},
        tca=result.tca,
        n_quotes=result.n_quotes,
        n_fills=result.n_fills,
        total_pnl=result.total_pnl,
        final_inventory=result.final_inventory,
    )

    return BacktestResponse(
        n_quotes=result.n_quotes,
        n_fills=result.n_fills,
        final_inventory=result.final_inventory,
        final_mid=result.final_mid,
        realised_pnl=result.realised_pnl,
        unrealised_pnl=result.unrealised_pnl,
        total_pnl=result.total_pnl,
        tca=TCABreakdown(**result.tca),
        mid_path_sample=mid_sample,
        inventory_path_sample=inv_sample,
        saved_id=saved_id,
    )


@router.post("/backtest/snapshots", response_model=BacktestResponse)
def backtest_snapshots(request: SnapshotsBacktestRequest) -> BacktestResponse:
    """Run a backtest over a caller-supplied list of OrderBookSnapshots.

    Any source (a CSV, an external adapter, a custom replay) can produce
    the input; the schema validator on each snapshot guarantees sortedness.
    """
    for snap in request.snapshots:
        if snap.symbol != request.config.symbol:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"snapshot.symbol {snap.symbol} != config.symbol "
                    f"{request.config.symbol}"
                ),
            )
    result = run_backtest(request.snapshots, request.config)
    return _make_backtest_response(request.config, result, source="snapshots")


@router.post("/backtest/live", response_model=BacktestResponse)
def backtest_live(request: LiveBacktestRequest) -> BacktestResponse:
    """Run a backtest by replaying a registered DataAdapter over [start, end].

    Adapter is instantiated from `adapter_kwargs`. Snapshots produced by the
    adapter are passed to `run_backtest` unchanged.
    """
    adapter_cls = _ADAPTER_REGISTRY.get(request.adapter)
    if adapter_cls is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"unknown adapter '{request.adapter}'. "
                f"Available: {sorted(_ADAPTER_REGISTRY)}"
            ),
        )
    try:
        adapter: DataAdapter = adapter_cls(**request.adapter_kwargs)
    except TypeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"adapter_kwargs not accepted by {request.adapter}: {exc}",
        )
    try:
        snapshots = list(
            adapter.replay(request.config.symbol, request.start, request.end)
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — surface adapter errors verbatim
        raise HTTPException(
            status_code=502,
            detail=f"adapter {request.adapter} replay failed: {exc}",
        )
    if len(snapshots) < 2:
        raise HTTPException(
            status_code=404,
            detail=f"adapter {request.adapter} produced only {len(snapshots)} snapshots",
        )
    result = run_backtest(snapshots, request.config)
    return _make_backtest_response(
        request.config, result, source=f"adapter:{request.adapter}"
    )


@router.get("/adapters", response_model=list[AdapterInfo])
def list_adapters() -> list[AdapterInfo]:
    """Enumerate adapters available to /backtest/live."""
    return [
        AdapterInfo(name=name, docstring=(cls.__doc__ or "").strip().split("\n")[0])
        for name, cls in _ADAPTER_REGISTRY.items()
    ]
