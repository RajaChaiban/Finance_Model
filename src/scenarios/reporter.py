"""
HTML report generation for scenario analysis results.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict
from src.scenarios.engine import StressResult


def generate_scenario_report(results: Dict[str, StressResult], config,
                            save_dir: str = "./reports/") -> str:
    """
    Generate HTML scenario analysis report.

    Args:
        results: Dict mapping scenario names to StressResults
        config: PricingConfig object
        save_dir: Directory to save report

    Returns:
        Path to saved HTML file
    """

    # Generate scenario rows
    scenario_rows = ""
    for scenario_name, result in results.items():
        status = "VIABLE" if result.viable else "AT RISK"
        status_color = "#10b981" if result.viable else "#ef4444"

        scenario_rows += f"""
        <tr>
            <td><strong>{result.scenario.name}</strong></td>
            <td>{result.scenario.description}</td>
            <td>${result.original_price:.4f}</td>
            <td>${result.stressed_price:.4f}</td>
            <td style="color: {'#10b981' if result.price_impact > 0 else '#ef4444'};">
                {result.price_impact_pct:+.2f}%
            </td>
            <td>{result.delta_shock:+.4f}</td>
            <td>{result.vega_shock:+.4f}</td>
            <td style="color: {status_color}; font-weight: bold;">{status}</td>
        </tr>
        """

    # Generate Greek shock matrix
    greek_shock_table = ""
    for scenario_name, result in results.items():
        greek_shock_table += f"""
        <tr>
            <td>{result.scenario.name}</td>
            <td>{result.delta_shock:+.4f}</td>
            <td>{result.gamma_shock:+.6f}</td>
            <td>{result.vega_shock:+.4f}</td>
            <td style="background: {'#f0fdf4' if result.viable else '#fef2f2'};">
                {'YES' if result.viable else 'NO'}
            </td>
        </tr>
        """

    # Generate heatmap data for visualization
    scenario_names = list(results.keys())
    price_impacts = [results[s].price_impact_pct for s in scenario_names]
    delta_shocks = [results[s].delta_shock for s in scenario_names]
    viability = [1 if results[s].viable else 0 for s in scenario_names]

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Scenario Analysis Report</title>
        <script src="https://cdn.plot.ly/plotly-latest.min.js"></script>
        <style>
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height: 100vh;
                padding: 20px;
            }}
            .container {{
                max-width: 1200px;
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
            .content {{
                padding: 40px;
            }}
            .section {{
                margin-bottom: 40px;
            }}
            .section h2 {{
                color: #667eea;
                font-size: 1.5em;
                margin-bottom: 20px;
                border-bottom: 2px solid #667eea;
                padding-bottom: 10px;
            }}
            .viability-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
                gap: 15px;
                margin-bottom: 30px;
            }}
            .viability-card {{
                padding: 15px;
                border-radius: 8px;
                text-align: center;
                border: 2px solid #e5e7eb;
            }}
            .viability-card.viable {{
                background: #f0fdf4;
                border-color: #10b981;
            }}
            .viability-card.at-risk {{
                background: #fef2f2;
                border-color: #ef4444;
            }}
            .viability-card.scenario {{
                font-weight: 600;
                color: #1f2937;
                margin-bottom: 8px;
            }}
            .viability-card.status {{
                font-weight: bold;
                font-size: 1.1em;
            }}
            .viability-card.viable .status {{
                color: #10b981;
            }}
            .viability-card.at-risk .status {{
                color: #ef4444;
            }}
            .chart {{
                margin: 20px 0;
                padding: 20px;
                background: #f9fafb;
                border-radius: 8px;
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                margin: 20px 0;
                background: white;
            }}
            th {{
                background: #f3f4f6;
                padding: 12px;
                text-align: left;
                font-weight: 600;
                color: #667eea;
                border-bottom: 2px solid #e5e7eb;
            }}
            td {{
                padding: 10px 12px;
                border-bottom: 1px solid #e5e7eb;
            }}
            tr:hover {{
                background: #f9fafb;
            }}
            .footer {{
                background: #f9fafb;
                padding: 20px 40px;
                text-align: center;
                color: #6b7280;
                border-top: 1px solid #e5e7eb;
            }}
            .product-spec {{
                background: #f0f4ff;
                padding: 15px;
                border-radius: 8px;
                margin-bottom: 20px;
            }}
            .spec-row {{
                display: grid;
                grid-template-columns: 1fr 1fr;
                gap: 20px;
                margin-bottom: 10px;
            }}
            .warning {{
                background: #fef3c7;
                border-left: 4px solid #f59e0b;
                padding: 15px;
                border-radius: 4px;
                margin: 15px 0;
            }}
            .alert {{
                background: #fee2e2;
                border-left: 4px solid #ef4444;
                padding: 15px;
                border-radius: 4px;
                margin: 15px 0;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Header -->
            <div class="header">
                <h1>Scenario Analysis Report</h1>
                <p>Stress Testing & Risk Validation | {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}</p>
            </div>

            <!-- Content -->
            <div class="content">
                <!-- Product Specification -->
                <div class="section">
                    <h2>Product Specification</h2>
                    <div class="product-spec">
                        <div class="spec-row">
                            <div>
                                <strong>Option Type:</strong> {config.option_type.replace('_', ' ').title()}<br>
                                <strong>Underlying:</strong> {config.underlying}<br>
                                <strong>Strike:</strong> ${config.strike_price:.2f}
                            </div>
                            <div>
                                <strong>Spot Price:</strong> ${config.spot_price:.2f}<br>
                                <strong>Volatility:</strong> {config.volatility:.2%}<br>
                                <strong>Time to Expiration:</strong> {config.days_to_expiration} days
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Viability Summary -->
                <div class="section">
                    <h2>Scenario Viability Matrix</h2>
                    <div class="viability-grid">
                        {chr(10).join([f'''
                        <div class="viability-card {'viable' if results[s].viable else 'at-risk'}">
                            <div class="scenario">{results[s].scenario.name}</div>
                            <div class="status">{'VIABLE' if results[s].viable else 'AT RISK'}</div>
                        </div>
                        ''' for s in scenario_names])}
                    </div>
                </div>

                <!-- Risk Summary -->
                <div class="section">
                    <h2>Risk Assessment</h2>
                    {chr(10).join([f'''
                    <div class="{'alert' if not results[s].viable else 'warning'}">
                        <strong>{results[s].scenario.name}:</strong> Price impact {results[s].price_impact_pct:+.2f}%,
                        Delta shift {results[s].delta_shock:+.4f}. Status: {'AT RISK' if not results[s].viable else 'MANAGEABLE'}
                    </div>
                    ''' for s in scenario_names if not results[s].viable or abs(results[s].price_impact_pct) > 10])}
                </div>

                <!-- Detailed Scenario Comparison -->
                <div class="section">
                    <h2>Detailed Scenario Analysis</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Scenario</th>
                                <th>Description</th>
                                <th>Original Price</th>
                                <th>Stressed Price</th>
                                <th>Price Impact</th>
                                <th>Delta Shock</th>
                                <th>Vega Shock</th>
                                <th>Viability</th>
                            </tr>
                        </thead>
                        <tbody>
                            {scenario_rows}
                        </tbody>
                    </table>
                </div>

                <!-- Greeks Under Stress -->
                <div class="section">
                    <h2>Greeks Sensitivity Matrix</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Scenario</th>
                                <th>Delta Shock</th>
                                <th>Gamma Shock</th>
                                <th>Vega Shock</th>
                                <th>Hedgeable</th>
                            </tr>
                        </thead>
                        <tbody>
                            {greek_shock_table}
                        </tbody>
                    </table>
                </div>

                <!-- Price Impact Chart -->
                <div class="section">
                    <h2>Price Impact Analysis</h2>
                    <div class="chart" id="impact-chart"></div>
                    <script>
                        var scenarios = {str(scenario_names)};
                        var impacts = {str(price_impacts)};

                        var trace = {{
                            x: scenarios,
                            y: impacts,
                            type: 'bar',
                            marker: {{
                                color: impacts.map(x => x > 0 ? '#10b981' : '#ef4444')
                            }}
                        }};

                        var layout = {{
                            title: 'Price Impact by Scenario',
                            xaxis: {{title: 'Scenario'}},
                            yaxis: {{title: 'Price Impact (%)'}},
                            plot_bgcolor: '#f9fafb',
                            height: 400,
                            margin: {{b: 120}}
                        }};

                        Plotly.newPlot('impact-chart', [trace], layout, {{responsive: true}});
                    </script>
                </div>

                <!-- Delta Shock Chart -->
                <div class="section">
                    <h2>Delta Sensitivity</h2>
                    <div class="chart" id="delta-chart"></div>
                    <script>
                        var scenarios = {str(scenario_names)};
                        var deltas = {str(delta_shocks)};

                        var trace = {{
                            x: scenarios,
                            y: deltas,
                            type: 'bar',
                            marker: {{color: '#667eea'}}
                        }};

                        var layout = {{
                            title: 'Delta Shock by Scenario',
                            xaxis: {{title: 'Scenario'}},
                            yaxis: {{title: 'Delta Change'}},
                            plot_bgcolor: '#f9fafb',
                            height: 400,
                            margin: {{b: 120}}
                        }};

                        Plotly.newPlot('delta-chart', [trace], layout, {{responsive: true}});
                    </script>
                </div>

                <!-- Summary -->
                <div class="section">
                    <h2>Summary & Recommendations</h2>
                    <p>
                        This scenario analysis stress-tests the {config.option_type.replace('_', ' ')} structure
                        across {len(results)} extreme market scenarios. The structure is viable in
                        <strong>{sum(1 for r in results.values() if r.viable)} out of {len(results)}</strong> scenarios.
                    </p>
                    <p style="margin-top: 10px;">
                        <strong>Hedging Implications:</strong> The position remains hedgeable across most scenarios with
                        managed Greeks behavior. Monitor particularly during volatility spikes and market crashes where
                        gamma and vega exposures concentrate.
                    </p>
                </div>
            </div>

            <!-- Footer -->
            <div class="footer">
                <p>This scenario analysis is for risk management purposes. Actual results may differ under stressed market conditions.</p>
                <p style="margin-top: 10px; font-size: 0.85em;">Generated by Institutional Derivatives Pipeline | {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            </div>
        </div>
    </body>
    </html>
    """

    # Save report
    save_path = Path(save_dir)
    save_path.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{config.underlying}_{config.option_type}_scenarios_{timestamp}.html"
    filepath = save_path / filename

    with open(filepath, "w") as f:
        f.write(html)

    return str(filepath)
