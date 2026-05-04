"""
HTML report generation for backtest results.
"""

from datetime import datetime
from pathlib import Path
from src.backtesting.engine import BacktestMetrics


def generate_backtest_report(metrics: BacktestMetrics, performance: dict,
                            config, save_dir: str = "./reports/") -> str:
    """
    Generate HTML backtest report.

    Args:
        metrics: BacktestMetrics object
        performance: Performance dict from backtest
        config: PricingConfig object
        save_dir: Directory to save report

    Returns:
        Path to saved HTML file
    """

    # Generate performance table rows
    perf_rows = ""
    for i in range(0, len(performance['dates']), max(1, len(performance['dates']) // 20)):
        date = performance['dates'][i]
        price = performance['underlying_prices'][i]
        intrinsic = performance['intrinsic_value'][i]
        pnl = performance['pnl'][i]
        ret = performance['returns'][i]

        perf_rows += f"""
        <tr>
            <td>{date}</td>
            <td>${price:.2f}</td>
            <td>${intrinsic:.2f}</td>
            <td>${pnl:+.2f}</td>
            <td>{ret:+.2f}%</td>
        </tr>
        """

    html = f"""
    <!DOCTYPE html>
    <html lang="en">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Backtest Report</title>
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
            .metrics-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 20px;
                margin-bottom: 30px;
            }}
            .metric-card {{
                background: #f9fafb;
                padding: 20px;
                border-radius: 8px;
                border-left: 4px solid #667eea;
            }}
            .metric-label {{
                color: #6b7280;
                font-size: 0.85em;
                text-transform: uppercase;
                margin-bottom: 8px;
            }}
            .metric-value {{
                font-size: 1.8em;
                font-weight: bold;
                color: #667eea;
            }}
            .positive {{
                color: #10b981;
            }}
            .negative {{
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
        </style>
    </head>
    <body>
        <div class="container">
            <!-- Header -->
            <div class="header">
                <h1>Backtest Report</h1>
                <p>Historical Performance Analysis | {datetime.now().strftime('%B %d, %Y at %H:%M:%S')}</p>
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
                                <strong>Entry Price:</strong> ${performance['entry_price']:.2f}<br>
                                <strong>Initial Premium:</strong> ${performance['initial_premium']:.4f}<br>
                                <strong>Initial Time to Expiration:</strong> {config.days_to_expiration} days
                            </div>
                        </div>
                    </div>
                </div>

                <!-- Key Metrics -->
                <div class="section">
                    <h2>Performance Metrics</h2>
                    <div class="metrics-grid">
                        <div class="metric-card">
                            <div class="metric-label">Total Return</div>
                            <div class="metric-value {('positive' if metrics.total_return > 0 else 'negative')}">{metrics.total_return:+.2f}%</div>
                        </div>
                        <div class="metric-card">
                            <div class="metric-label">Hit Rate</div>
                            <div class="metric-value positive">{metrics.hit_rate:.1f}%</div>
                        </div>
                        <div class="metric-card">
                            <div class="metric-label">Avg P&L</div>
                            <div class="metric-value {('positive' if metrics.avg_pnl > 0 else 'negative')}">${metrics.avg_pnl:+.2f}</div>
                        </div>
                        <div class="metric-card">
                            <div class="metric-label">Max Drawdown</div>
                            <div class="metric-value negative">{metrics.max_drawdown:.2f}%</div>
                        </div>
                        <div class="metric-card">
                            <div class="metric-label">Sharpe Ratio</div>
                            <div class="metric-value">{metrics.sharpe_ratio:.2f}</div>
                        </div>
                        <div class="metric-card">
                            <div class="metric-label">Sortino Ratio</div>
                            <div class="metric-value">{metrics.sortino_ratio:.2f}</div>
                        </div>
                        <div class="metric-card">
                            <div class="metric-label">Win/Loss Ratio</div>
                            <div class="metric-value positive">{metrics.win_loss_ratio:.2f}</div>
                        </div>
                        <div class="metric-card">
                            <div class="metric-label">Max P&L</div>
                            <div class="metric-value positive">${metrics.max_pnl:+.2f}</div>
                        </div>
                    </div>
                </div>

                <!-- Performance Chart -->
                <div class="section">
                    <h2>P&L Over Time</h2>
                    <div class="chart" id="pnl-chart"></div>
                    <script>
                        var dates = {performance['dates']};
                        var pnl = {performance['pnl']};
                        var prices = {performance['underlying_prices']};

                        var trace1 = {{
                            x: dates,
                            y: pnl,
                            type: 'scatter',
                            mode: 'lines',
                            name: 'P&L',
                            yaxis: 'y1'
                        }};

                        var trace2 = {{
                            x: dates,
                            y: prices,
                            type: 'scatter',
                            mode: 'lines',
                            name: 'Underlying Price',
                            yaxis: 'y2'
                        }};

                        var data = [trace1, trace2];

                        var layout = {{
                            title: 'P&L vs Underlying Price',
                            xaxis: {{title: 'Date'}},
                            yaxis: {{title: 'P&L ($)', side: 'left'}},
                            yaxis2: {{title: 'Underlying ($)', side: 'right', overlaying: 'y'}},
                            hovermode: 'x unified',
                            plot_bgcolor: '#f9fafb',
                            height: 500
                        }};

                        Plotly.newPlot('pnl-chart', data, layout, {{responsive: true}});
                    </script>
                </div>

                <!-- Performance Data Table -->
                <div class="section">
                    <h2>Historical Performance Data</h2>
                    <table>
                        <thead>
                            <tr>
                                <th>Date</th>
                                <th>Underlying Price</th>
                                <th>Intrinsic Value</th>
                                <th>P&L</th>
                                <th>Return</th>
                            </tr>
                        </thead>
                        <tbody>
                            {perf_rows}
                        </tbody>
                    </table>
                </div>

                <!-- Summary -->
                <div class="section">
                    <h2>Summary</h2>
                    <p>
                        This backtest validates the {config.option_type.replace('_', ' ')} structure over {len(performance['dates'])} trading days.
                        The structure achieved a {metrics.total_return:+.2f}% return with a {metrics.hit_rate:.1f}% hit rate
                        (percentage of days the position was profitable).
                    </p>
                    <p style="margin-top: 10px;">
                        <strong>Risk Assessment:</strong> Maximum drawdown of {metrics.max_drawdown:.2f}% with a Sharpe ratio of {metrics.sharpe_ratio:.2f}.
                        This indicates the structure performed {'well' if metrics.sharpe_ratio > 1 else 'adequately'} on a risk-adjusted basis.
                    </p>
                </div>
            </div>

            <!-- Footer -->
            <div class="footer">
                <p>This backtest is for informational purposes. Historical performance does not guarantee future results.</p>
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
    filename = f"{config.underlying}_{config.option_type}_backtest_{timestamp}.html"
    filepath = save_path / filename

    with open(filepath, "w") as f:
        f.write(html)

    return str(filepath)
