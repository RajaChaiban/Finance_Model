"""Report generation: creates HTML reports with pricing results and Greeks."""

import numpy as np
import matplotlib.pyplot as plt
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional
import base64
from io import BytesIO
from jinja2 import Template


def generate_report(results: Dict[str, Any], config: Any, show_plot: bool = False) -> str:
    """Generate HTML report with pricing results, Greeks, and sensitivity analysis.

    Args:
        results: Dict with keys:
            - price: option price
            - greeks: dict of Greeks
            - paths: stock price paths (optional, for MC methods)
            - std_error: standard error (optional)
        config: PricingConfig object
        show_plot: If True, also display the plot

    Returns:
        Path to saved HTML file
    """
    # Create charts
    chart_html = _create_charts(results.get("paths"), config)

    # Format Greeks table
    greeks_data = results.get("greeks", {})
    greek_rows = []
    for greek_name in ["delta", "gamma", "vega", "theta", "rho"]:
        if greek_name in greeks_data:
            value = greeks_data[greek_name]
            if greek_name == "delta":
                formatted = f"{value:.4f}"
            elif greek_name == "gamma":
                formatted = f"{value:.6f}"
            elif greek_name == "vega":
                formatted = f"{value:.4f}"
            elif greek_name == "theta":
                formatted = f"{value:.4f}"
            elif greek_name == "rho":
                formatted = f"{value:.4f}"
            greek_rows.append((greek_name.upper(), formatted))

    # Early exercise premium for American puts
    early_exercise_html = ""
    if "early_exercise_premium" in greeks_data:
        premium = greeks_data["early_exercise_premium"]
        premium_pct = greeks_data.get("early_exercise_premium_pct", 0)
        early_exercise_html = f"""
        <div class="result-box">
            <h3>Early Exercise Premium</h3>
            <p><strong>${premium:.4f}</strong> ({premium_pct:.2f}%)</p>
            <p>Value of optionality to exercise before expiration</p>
        </div>
        """

    # Confidence interval for MC methods
    confidence_html = ""
    if results.get("std_error"):
        std_err = results["std_error"]
        price = results["price"]
        ci_lower = price - 1.96 * std_err
        ci_upper = price + 1.96 * std_err
        confidence_html = f"""
        <div class="result-box">
            <h3>95% Confidence Interval</h3>
            <p>${ci_lower:.4f} to ${ci_upper:.4f}</p>
            <p>Standard Error: ${std_err:.4f}</p>
        </div>
        """

    # Build HTML
    html_content = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Option Pricing Report</title>
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                max-width: 1200px;
                margin: 0 auto;
                padding: 20px;
                background-color: #f5f5f5;
                color: #333;
            }}
            .header {{
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: white;
                padding: 30px;
                border-radius: 8px;
                margin-bottom: 30px;
            }}
            .header h1 {{
                margin: 0;
                font-size: 2.5em;
            }}
            .header p {{
                margin: 5px 0 0 0;
                font-size: 0.95em;
                opacity: 0.9;
            }}
            .summary {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .summary-card {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }}
            .summary-card h3 {{
                margin: 0 0 10px 0;
                color: #667eea;
                font-size: 0.9em;
                text-transform: uppercase;
                letter-spacing: 1px;
            }}
            .summary-card .value {{
                font-size: 1.8em;
                font-weight: bold;
                color: #333;
            }}
            .result-box {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
                margin-bottom: 20px;
            }}
            .result-box h3 {{
                margin-top: 0;
                color: #667eea;
                border-bottom: 2px solid #667eea;
                padding-bottom: 10px;
            }}
            .greeks-table {{
                width: 100%;
                border-collapse: collapse;
                font-size: 0.95em;
            }}
            .greeks-table th {{
                background-color: #667eea;
                color: white;
                padding: 12px;
                text-align: left;
                font-weight: 600;
            }}
            .greeks-table td {{
                padding: 12px;
                border-bottom: 1px solid #eee;
            }}
            .greeks-table tr:hover {{
                background-color: #f9f9f9;
            }}
            .section {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 20px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }}
            .section h2 {{
                margin-top: 0;
                color: #667eea;
                border-bottom: 2px solid #f0f0f0;
                padding-bottom: 10px;
            }}
            .charts {{
                background: white;
                padding: 20px;
                border-radius: 8px;
                margin-bottom: 20px;
                box-shadow: 0 2px 8px rgba(0,0,0,0.1);
            }}
            .chart-img {{
                max-width: 100%;
                height: auto;
            }}
            .footer {{
                text-align: center;
                color: #999;
                font-size: 0.85em;
                margin-top: 40px;
                padding-top: 20px;
                border-top: 1px solid #eee;
            }}
            .method-badge {{
                display: inline-block;
                background-color: #e8eaf6;
                color: #667eea;
                padding: 5px 12px;
                border-radius: 20px;
                font-size: 0.85em;
                margin-top: 10px;
            }}
        </style>
    </head>
    <body>
        <div class="header">
            <h1>Option Pricing Report</h1>
            <p>Generated on {datetime.now().strftime("%B %d, %Y at %H:%M:%S")}</p>
        </div>

        <div class="summary">
            <div class="summary-card">
                <h3>Option Type</h3>
                <div class="value" style="font-size: 1.2em; text-transform: capitalize;">
                    {config.option_type.replace("_", " ")}
                </div>
            </div>
            <div class="summary-card">
                <h3>Underlying</h3>
                <div class="value">{config.underlying}</div>
            </div>
            <div class="summary-card">
                <h3>Strike Price</h3>
                <div class="value">${config.strike_price:.2f}</div>
            </div>
            <div class="summary-card">
                <h3>Days to Expiration</h3>
                <div class="value">{config.days_to_expiration}</div>
            </div>
        </div>

        <div class="result-box">
            <h3>Option Price</h3>
            <p style="font-size: 2em; margin: 0; color: #667eea;"><strong>${results.get("price", "N/A"):.4f}</strong></p>
            {confidence_html}
            {early_exercise_html}
        </div>

        <div class="section">
            <h2>Greeks (Risk Sensitivities)</h2>
            <table class="greeks-table">
                <tr>
                    <th>Greek</th>
                    <th>Value</th>
                    <th>Interpretation</th>
                </tr>
    """

    # Add Greek rows
    greek_interpretations = {
        "DELTA": "Price change per $1 move in underlying",
        "GAMMA": "Delta sensitivity (convexity)",
        "VEGA": "Price change per 1% volatility move",
        "THETA": "Daily time decay",
        "RHO": "Price sensitivity to interest rates"
    }

    for greek_name, value in greek_rows:
        interp = greek_interpretations.get(greek_name, "")
        html_content += f"""
                <tr>
                    <td><strong>{greek_name}</strong></td>
                    <td>{value}</td>
                    <td>{interp}</td>
                </tr>
        """

    html_content += """
            </table>
        </div>

        <div class="section">
            <h2>Pricing Parameters</h2>
            <table class="greeks-table">
                <tr>
                    <th>Parameter</th>
                    <th>Value</th>
                </tr>
    """

    # Parameter table
    params = [
        ("Spot Price", f"${config.spot_price:.2f}"),
        ("Strike Price", f"${config.strike_price:.2f}"),
        ("Volatility", f"{config.volatility:.2%}"),
        ("Risk-Free Rate", f"{config.risk_free_rate:.2%}"),
        ("Dividend Yield", f"{config.dividend_yield:.2%}"),
        ("Time to Expiration", f"{config.days_to_expiration} days ({config.days_to_expiration/365:.4f} years)"),
    ]

    if config.barrier_level:
        params.append(("Barrier Level", f"${config.barrier_level:.2f}"))

    for param_name, value in params:
        html_content += f"""
                <tr>
                    <td><strong>{param_name}</strong></td>
                    <td>{value}</td>
                </tr>
        """

    html_content += """
            </table>
        </div>
    """

    # Add charts if available
    if results.get("paths") is not None:
        html_content += f"""
        <div class="charts">
            <h2>Analysis Charts</h2>
            {chart_html}
        </div>
        """

    html_content += f"""
        <div class="footer">
            <p>Pricing Method: {results.get("method", "Not specified")}</p>
            <p>This report is for informational purposes only and not financial advice.</p>
        </div>
    </body>
    </html>
    """

    # Save report
    save_dir = Path(config.save_to)
    save_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{config.underlying}_{config.option_type}_{timestamp}.html"
    filepath = save_dir / filename

    with open(filepath, "w") as f:
        f.write(html_content)

    return str(filepath)


def _create_charts(paths: Optional[np.ndarray], config: Any) -> str:
    """Create matplotlib charts and return as base64-encoded image HTML.

    Args:
        paths: Stock price paths from MC simulation (n_paths x n_steps+1)
        config: PricingConfig object

    Returns:
        HTML img tag with embedded PNG
    """
    if paths is None:
        return "<p>No path data available</p>"

    fig, axes = plt.subplots(2, 2, figsize=(14, 10))
    fig.suptitle(f"Analysis: {config.underlying} {config.option_type.replace('_', ' ').title()}", fontsize=14)

    # Plot 1: Sample paths
    ax = axes[0, 0]
    for i in range(min(50, paths.shape[0])):
        ax.plot(paths[i, :], alpha=0.1, color='blue')
    ax.axhline(y=config.strike_price, color='red', linestyle='--', linewidth=2, label=f'Strike = ${config.strike_price:.0f}')
    ax.axhline(y=config.spot_price, color='green', linestyle='--', linewidth=2, label=f'Spot = ${config.spot_price:.0f}')
    ax.set_xlabel('Days')
    ax.set_ylabel('Stock Price ($)')
    ax.set_title('Sample MC Paths (50 of many)')
    ax.legend()
    ax.grid(True, alpha=0.3)

    # Plot 2: Final price distribution
    ax = axes[0, 1]
    final_prices = paths[:, -1]
    ax.hist(final_prices, bins=50, edgecolor='black', alpha=0.7, color='blue')
    ax.axvline(x=config.strike_price, color='red', linestyle='--', linewidth=2, label='Strike')
    ax.axvline(x=config.spot_price, color='green', linestyle='--', linewidth=2, label='Spot')
    ax.set_xlabel('Final Price ($)')
    ax.set_ylabel('Frequency')
    ax.set_title('Distribution of Final Prices')
    ax.legend()
    ax.grid(True, alpha=0.3, axis='y')

    # Plot 3: Payoff distribution
    ax = axes[1, 0]
    payoffs = np.maximum(config.strike_price - final_prices, 0)  # Put payoff
    ax.hist(payoffs, bins=50, edgecolor='black', alpha=0.7, color='orange')
    ax.set_xlabel('Payoff ($)')
    ax.set_ylabel('Frequency')
    ax.set_title('Distribution of Option Payoffs')
    ax.grid(True, alpha=0.3, axis='y')

    # Plot 4: Price path statistics
    ax = axes[1, 1]
    ax.text(0.1, 0.9, 'Path Statistics', fontsize=12, fontweight='bold', transform=ax.transAxes)

    min_prices = np.min(paths, axis=1)
    max_prices = np.max(paths, axis=1)

    stats_text = f"""
    Mean Final Price: ${final_prices.mean():.2f}
    Std Final Price: ${final_prices.std():.2f}

    Mean Min Price: ${min_prices.mean():.2f}
    Mean Max Price: ${max_prices.mean():.2f}

    ITM Probability: {100*np.sum(payoffs > 0)/len(payoffs):.1f}%
    Avg Payoff if ITM: ${payoffs[payoffs > 0].mean():.2f}
    """

    ax.text(0.1, 0.75, stats_text, fontsize=10, family='monospace', transform=ax.transAxes, verticalalignment='top')
    ax.axis('off')

    plt.tight_layout()

    # Convert to base64
    buffer = BytesIO()
    plt.savefig(buffer, format='png', dpi=100, bbox_inches='tight')
    buffer.seek(0)
    image_base64 = base64.b64encode(buffer.read()).decode()
    plt.close()

    return f'<img src="data:image/png;base64,{image_base64}" class="chart-img" />'
