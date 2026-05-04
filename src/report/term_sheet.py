"""Phase 9 — PRIIPs-style term sheet PDF generator.

Produces a client-facing PDF with product summary, key terms, and an
indicative scenario block (favourable / moderate / unfavourable / stress).
Built on reportlab; no external services required.
"""
from __future__ import annotations

from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

from src.agents.state import Structure


def generate_term_sheet(
    *,
    structure: Structure,
    mid_price: float,
    scenarios: dict[str, float],
    output_path: str,
) -> str:
    """Generate a PRIIPs-style term sheet PDF.

    Parameters
    ----------
    structure:
        The `Structure` Pydantic object defining the product terms.
    mid_price:
        Indicative mid-market valuation in USD.
    scenarios:
        Mapping of scenario label → worst-of performance scalar
        (e.g. ``{"favourable": 1.30, "moderate": 1.05, ...}``).
    output_path:
        Absolute path for the output PDF file.

    Returns
    -------
    str
        The ``output_path`` that was written, for caller convenience.
    """
    doc = SimpleDocTemplate(output_path, pagesize=A4)
    styles = getSampleStyleSheet()
    story = []

    # ------------------------------------------------------------------ header
    story.append(
        Paragraph(f"<b>Term Sheet — {structure.name}</b>", styles["Title"])
    )
    story.append(
        Paragraph(f"Issue date: {date.today().isoformat()}", styles["Normal"])
    )
    story.append(Spacer(1, 0.4 * cm))

    # ------------------------------------------------------------ product facts
    facts: list[list[str]] = [
        ["Notional", f"USD {structure.notional:,.0f}"],
        ["Maturity", f"{structure.maturity_years:.1f} year(s)"],
        ["Indicative mid", f"USD {mid_price:,.0f}"],
        ["Number of legs", str(len(structure.legs))],
    ]

    if structure.autocall_terms:
        t = structure.autocall_terms
        facts += [
            ["Coupon rate", f"{t.coupon_rate * 100:.2f}% per observation"],
            ["Autocall barrier", f"{t.autocall_barrier * 100:.0f}% of initial"],
            ["Coupon barrier", f"{t.coupon_barrier * 100:.0f}% of initial"],
            ["Protection barrier", f"{t.protection_barrier * 100:.0f}% of initial"],
            ["Memory coupon", "Yes" if t.memory else "No"],
        ]

    if structure.observation_schedule:
        obs = structure.observation_schedule
        facts.append(["Observation dates", f"{len(obs.dates_years)} (quarterly)"])
        facts.append(
            [
                "Final observation",
                f"{obs.dates_years[-1]:.2f}y",
            ]
        )

    tbl = Table(facts, colWidths=[6.5 * cm, 9 * cm])
    tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
                ("BACKGROUND", (0, 0), (-1, 0), colors.whitesmoke),
                ("ROWBACKGROUNDS", (0, 0), (-1, -1), [colors.white, colors.whitesmoke]),
            ]
        )
    )
    story.append(tbl)
    story.append(Spacer(1, 0.6 * cm))

    # --------------------------------------------------------- leg summary table
    story.append(Paragraph("<b>Structure Legs</b>", styles["Heading2"]))
    leg_rows = [["#", "Side", "Instrument", "Quantity", "Strike", "Barrier"]]
    for idx, leg in enumerate(structure.legs, start=1):
        leg_rows.append(
            [
                str(idx),
                leg.side.title(),
                leg.instrument_kind.replace("_", " ").title(),
                f"{leg.quantity:.2f}",
                f"{leg.strike:.4f}" if leg.strike is not None else "—",
                f"{leg.barrier:.4f}" if leg.barrier is not None else "—",
            ]
        )
    leg_tbl = Table(
        leg_rows,
        colWidths=[1 * cm, 2.5 * cm, 5 * cm, 2.5 * cm, 2.5 * cm, 2.5 * cm],
    )
    leg_tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ]
        )
    )
    story.append(leg_tbl)
    story.append(Spacer(1, 0.6 * cm))

    # ------------------------------------------------------- PRIIPs scenarios
    story.append(Paragraph("<b>Indicative Performance Scenarios</b>", styles["Heading2"]))
    story.append(
        Paragraph(
            "The figures below are estimates of future performance based on "
            "evidence from the past. They are not an exact indicator of what "
            "you will get back. What you get back will depend on how the market "
            "performs and how long you keep the product.",
            styles["Normal"],
        )
    )
    story.append(Spacer(1, 0.3 * cm))

    rows = [["Scenario", "Worst-of performance", "Indicative payoff (USD)", "Return on notional"]]
    for label, perf in scenarios.items():
        # Placeholder payoff calculation (non-PRIIPs-accurate; indicative only):
        #   perf >= protection_barrier  → return notional (+ approx coupon income)
        #   perf <  protection_barrier  → capital loss proportional to perf
        protection = (
            structure.autocall_terms.protection_barrier
            if structure.autocall_terms
            else 0.0
        )
        coupon_income = (
            structure.autocall_terms.coupon_rate * structure.maturity_years
            if structure.autocall_terms
            else 0.0
        )
        if perf >= protection:
            payoff = structure.notional * (1.0 + coupon_income)
        else:
            # Capital loss + pro-rata coupon on survived periods
            payoff = structure.notional * perf
        ret_pct = (payoff / structure.notional - 1.0) * 100.0
        rows.append(
            [
                label.title(),
                f"{perf * 100:.0f}%",
                f"USD {payoff:,.0f}",
                f"{ret_pct:+.1f}%",
            ]
        )

    sc_tbl = Table(
        rows,
        colWidths=[4 * cm, 4.5 * cm, 5.5 * cm, 4 * cm],
    )
    sc_tbl.setStyle(
        TableStyle(
            [
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                (
                    "ROWBACKGROUNDS",
                    (0, 1),
                    (-1, -1),
                    [colors.white, colors.whitesmoke],
                ),
            ]
        )
    )
    story.append(sc_tbl)
    story.append(Spacer(1, 0.6 * cm))

    # --------------------------------------------------------------- disclaimer
    story.append(
        Paragraph(
            "<i>This document is indicative only and does not constitute an "
            "offer to buy or sell any security. Final terms will be set in the "
            "trade confirmation. Past performance is not a reliable indicator "
            "of future results. Capital is at risk.</i>",
            styles["Italic"],
        )
    )

    doc.build(story)
    return output_path
