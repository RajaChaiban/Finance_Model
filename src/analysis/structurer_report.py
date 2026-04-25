"""Generate HTML report for Structurer Review."""

from datetime import datetime
from pathlib import Path
from src.analysis.structurer_agent import StructurerOpinion


def generate_structurer_report(opinion: StructurerOpinion, config, save_dir: str = "./reports/") -> str:
    """Generate beautiful HTML report with structurer analysis.

    Args:
        opinion: StructurerOpinion object from structurer review
        config: PricingConfig object
        save_dir: Directory to save report

    Returns:
        Path to saved HTML file
    """
    # Determine recommendation color and label
    rec_colors = {
        "STRONG_BUY": ("#10b981", "STRONG BUY"),
        "BUY": ("#3b82f6", "BUY"),
        "HOLD": ("#f59e0b", "HOLD"),
        "SELL": ("#ef4444", "SELL"),
        "STRONG_SELL": ("#dc2626", "STRONG SELL"),
    }

    rec_color, rec_label = rec_colors.get(opinion.recommendation, ("#6b7280", "HOLD"))

    # Risk level color
    risk_colors = {
        1: "#10b981",  # green
        2: "#10b981",
        3: "#3b82f6",  # blue
        4: "#3b82f6",
        5: "#f59e0b",  # orange
        6: "#f59e0b",
        7: "#ef4444",  # red
        8: "#ef4444",
        9: "#dc2626",  # dark red
        10: "#dc2626",
    }
    risk_color = risk_colors.get(opinion.risk_score, "#6b7280")

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Structurer Review Report</title>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Helvetica Neue', sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }}
            .container {{
                max-width: 1000px;
                margin: 0 auto;
                background: white;
                border-radius: 12px;
                box-shadow: 0 20px 60px rgba(0,0,0,0.3);
                overflow: hidden;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 40px;
                text-align: center;
            }}
            .header h1 {{
                font-size: 2.5em;
                margin-bottom: 10px;
            }}
            .header p {{
                font-size: 1em;
                opacity: 0.9;
            }}
            .recommendation-banner {{
                background: {rec_color};
                color: white;
                padding: 30px;
                text-align: center;
                font-size: 1.8em;
                font-weight: bold;
                border-bottom: 4px solid {rec_color};
            }}
            .grid-2 {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 30px;
                padding: 40px;
            }}
            @media (max-width: 900px) {{
                .grid-2 {{
                    grid-template-columns: 1fr;
                }}
            }}
            .card {{
                border-radius: 8px;
                padding: 25px;
                background: #f9fafb;
                border-left: 4px solid #667eea;
            }}
            .card h3 {{
                color: #667eea;
                margin-bottom: 15px;
                font-size: 1.2em;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            .card p {{
                line-height: 1.6;
                color: #374151;
                margin-bottom: 10px;
            }}
            .price-display {{
                font-size: 2em;
                font-weight: bold;
                color: #667eea;
                margin: 10px 0;
            }}
            .edge {{
                font-size: 1.3em;
                margin: 10px 0;
                padding: 10px;
                background: white;
                border-radius: 6px;
            }}
            .edge.positive {{
                color: #10b981;
                border-left: 4px solid #10b981;
            }}
            .edge.negative {{
                color: #ef4444;
                border-left: 4px solid #ef4444;
            }}
            .section {{
                padding: 40px;
                border-top: 1px solid #e5e7eb;
            }}
            .section h2 {{
                color: #667eea;
                margin-bottom: 20px;
                font-size: 1.5em;
            }}
            .greeks-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 20px;
            }}
            .greek-card {{
                background: white;
                padding: 15px;
                border-radius: 6px;
                border: 1px solid #e5e7eb;
                text-align: center;
            }}
            .greek-label {{
                font-weight: bold;
                color: #667eea;
                font-size: 0.9em;
                text-transform: uppercase;
                margin-bottom: 5px;
            }}
            .greek-assessment {{
                color: #6b7280;
                font-size: 0.9em;
                line-height: 1.4;
            }}
            .risk-bar {{
                background: #e5e7eb;
                border-radius: 4px;
                overflow: hidden;
                height: 30px;
                margin: 10px 0;
            }}
            .risk-fill {{
                background: {risk_color};
                height: 100%;
                width: {opinion.risk_score * 10}%;
                display: flex;
                align-items: center;
                justify-content: center;
                color: white;
                font-weight: bold;
                font-size: 0.9em;
            }}
            .analysis-list {{
                list-style: none;
            }}
            .analysis-list li {{
                padding: 10px 0;
                border-bottom: 1px solid #f3f4f6;
                color: #374151;
            }}
            .analysis-list li:last-child {{
                border-bottom: none;
            }}
            .analysis-list li strong {{
                color: #667eea;
            }}
            .exec-summary {{
                background: linear-gradient(135deg, rgba(102, 126, 234, 0.1), rgba(118, 75, 162, 0.1));
                padding: 25px;
                border-radius: 8px;
                border-left: 4px solid #667eea;
                line-height: 1.8;
                color: #1f2937;
                font-size: 1.05em;
            }}
            .footer {{
                background: #f9fafb;
                padding: 20px 40px;
                text-align: center;
                color: #6b7280;
                font-size: 0.9em;
                border-top: 1px solid #e5e7eb;
            }}
            .value-box {{
                background: white;
                padding: 15px;
                border-radius: 6px;
                margin: 10px 0;
                border: 1px solid #e5e7eb;
            }}
            .label {{
                color: #6b7280;
                font-size: 0.85em;
                text-transform: uppercase;
                letter-spacing: 0.5px;
                margin-bottom: 5px;
            }}
            .value {{
                font-size: 1.5em;
                font-weight: bold;
                color: #667eea;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Header -->
            <div class="header">
                <h1>Structurer Review Report</h1>
                <p>Senior VP Analysis | {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}</p>
            </div>

            <!-- Recommendation Banner -->
            <div class="recommendation-banner">
                {rec_label}
            </div>

            <!-- Main Grid -->
            <div class="grid-2">
                <!-- Left: Pricing -->
                <div class="card">
                    <h3>Fair Value Assessment</h3>
                    <div class="price-display">${opinion.fair_value:.4f}</div>
                    <p class="label">Model Fair Value</p>

                    {f'''<div class="value-box">
                        <div class="label">Market Bid</div>
                        <div class="value">${opinion.market_bid:.2f}</div>
                    </div>
                    <div class="value-box">
                        <div class="label">Market Mid</div>
                        <div class="value">${opinion.market_mid:.2f}</div>
                    </div>
                    <div class="value-box">
                        <div class="label">Market Ask</div>
                        <div class="value">${opinion.market_ask:.2f}</div>
                    </div>''' if opinion.market_mid else '<p style="color: #6b7280;">Market data not available</p>'}

                    <div class="edge {'positive' if opinion.edge_pct > 0 else 'negative'}">
                        {opinion.edge_pct:+.2f}% Edge vs Market
                    </div>
                </div>

                <!-- Right: Summary -->
                <div class="card">
                    <h3>Executive Summary</h3>
                    <div class="exec-summary">
                        {opinion.executive_summary}
                    </div>
                    <div style="margin-top: 20px;">
                        <p><strong>Recommended Action:</strong> {opinion.recommended_action}</p>
                        <p style="margin-top: 10px; color: #6b7280; font-size: 0.95em;">{opinion.hedge_recommendation}</p>
                    </div>
                </div>
            </div>

            <!-- Greeks Section -->
            <div class="section">
                <h2>Greeks Analysis (Risk Sensitivities)</h2>
                <div class="greeks-grid">
                    {_render_greeks_cards(opinion.greeks_assessment)}
                </div>
            </div>

            <!-- Risk Assessment -->
            <div class="section">
                <h2>Risk Assessment</h2>
                <div style="margin: 20px 0;">
                    <p style="margin-bottom: 10px;"><strong>Risk Score: {opinion.risk_score}/10</strong></p>
                    <div class="risk-bar">
                        <div class="risk-fill">{opinion.risk_score}/10</div>
                    </div>
                </div>

                <div class="grid-2" style="margin-top: 20px;">
                    <div class="value-box">
                        <div class="label">Probability of Profit</div>
                        <div class="value" style="color: #10b981;">{opinion.probability_of_profit:.1f}%</div>
                    </div>
                    <div class="value-box">
                        <div class="label">Moneyness Status</div>
                        <div class="value" style="color: #f59e0b;">{opinion.moneyness_status}</div>
                    </div>
                </div>
            </div>

            <!-- Detailed Analysis -->
            <div class="section">
                <h2>Detailed Analysis</h2>
                <ul class="analysis-list">
                    {_render_analysis_list(opinion.detailed_analysis)}
                </ul>
            </div>

            <!-- Footer -->
            <div class="footer">
                <p>This analysis is generated by the Structurer Review Agent for informational purposes.</p>
                <p>Not financial advice. Consult with compliance before trading.</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Save report
    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{config.underlying}_{config.option_type}_structurer_{timestamp}.html"
    filepath = save_path / filename

    with open(filepath, "w") as f:
        f.write(html)

    return str(filepath)

def _render_greeks_cards(greeks: dict) -> str:
    """Render Greek assessment cards."""
    html = ""
    for greek, assessment in greeks.items():
        html += f"""
        <div class="greek-card">
            <div class="greek-label">{greek}</div>
            <div class="greek-assessment">{assessment}</div>
        </div>
        """
    return html

def _render_analysis_list(analysis: list) -> str:
    """Render detailed analysis as list items."""
    html = ""
    for item in analysis:
        if item == "":
            html += "<li style='margin: 5px 0;'></li>"
        elif item.startswith("STRONG") or item.startswith("FAIR") or item.startswith("GREEKS") or item.startswith("RISK"):
            html += f"<li style='font-weight: bold; margin-top: 15px; color: #667eea;'>{item}</li>"
        else:
            html += f"<li>{item}</li>"
    return html

