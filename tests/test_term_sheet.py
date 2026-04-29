"""Phase 9 — Term sheet PDF generator tests."""
from src.agents.state import Structure, StructureLeg, AutocallTerms, ObservationSchedule
from src.report.term_sheet import generate_term_sheet


def test_term_sheet_generates(tmp_path):
    s = Structure(
        name="3y SPX/NDX/RUT 10% Phoenix",
        legs=[StructureLeg(side="long", quantity=1.0, instrument_kind="zero_coupon")],
        maturity_years=3.0,
        notional=1_000_000.0,
        autocall_terms=AutocallTerms(
            coupon_rate=0.10,
            autocall_barrier=1.0,
            coupon_barrier=0.7,
            protection_barrier=0.6,
        ),
        observation_schedule=ObservationSchedule.quarterly(3.0),
    )
    out = tmp_path / "ts.pdf"
    path = generate_term_sheet(
        structure=s,
        mid_price=1_005_000.0,
        scenarios={
            "favourable": 1.30,
            "moderate": 1.05,
            "unfavourable": 0.85,
            "stress": 0.55,
        },
        output_path=str(out),
    )
    assert out.exists() and out.stat().st_size > 1000
