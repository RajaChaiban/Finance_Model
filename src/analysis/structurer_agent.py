"""Structurer Financial Analyst Review Agent - Senior VP perspective on pricing results."""

import numpy as np
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from datetime import datetime


@dataclass
class StructurerOpinion:
    """Structured output from structurer analysis."""
    recommendation: str  # strong_buy, buy, hold, sell, strong_sell
    fair_value: float
    market_bid: Optional[float]
    market_mid: Optional[float]
    market_ask: Optional[float]
    edge_pct: float
    risk_score: int  # 1-10, 10 is highest risk
    probability_of_profit: float  # percentage
    greeks_assessment: Dict[str, str]
    moneyness_status: str
    recommended_action: str
    hedge_recommendation: str
    executive_summary: str
    detailed_analysis: list


class StructurerReview:
    """Senior VP Structurer analysis of derivatives pricing results."""

    def analyze(self, results: Dict, config, market_bid: Optional[float] = None,
                market_ask: Optional[float] = None) -> StructurerOpinion:
        """Analyze pricing results from a Structurer's perspective.

        Args:
            results: Dict from pricing pipeline with keys:
                - price: option price
                - greeks: dict of Greeks
                - std_error: standard error (for MC methods)
                - paths: stock price paths (for Monte Carlo)
                - method: pricing method used
            config: PricingConfig object
            market_bid: Market bid price (optional, fetched if None)
            market_ask: Market ask price (optional, fetched if None)

        Returns:
            StructurerOpinion with recommendation and analysis
        """
        model_price = results["price"]
        greeks = results.get("greeks", {})
        std_error = results.get("std_error", 0)
        paths = results.get("paths")

        # Fetch market prices if not provided
        if market_bid is None or market_ask is None:
            market_bid, market_mid, market_ask = self._fetch_market_prices(
                config.underlying, config.strike_price, config.days_to_expiration
            )
        else:
            market_mid = (market_bid + market_ask) / 2

        # Calculate edge
        edge_pct = self._calculate_edge(model_price, market_mid, market_bid, market_ask)

        # Greeks assessment
        greeks_assessment = self._assess_greeks(greeks, config)

        # Probability of profit
        prob_profit = self._probability_of_profit(
            config.spot_price, config.strike_price, model_price, paths, config.option_type
        )

        # Moneyness
        moneyness = self._assess_moneyness(config.spot_price, config.strike_price, config.option_type)

        # Risk score
        risk_score = self._calculate_risk_score(greeks, config, std_error)

        # Trading recommendation
        recommendation, action = self._generate_recommendation(
            model_price, market_mid, market_bid, market_ask, edge_pct, risk_score,
            config.days_to_expiration, moneyness, greeks.get("delta", 0)
        )

        # Hedge recommendation
        hedge_rec = self._hedge_strategy(config.option_type, greeks, config)

        # Executive summary
        exec_summary = self._executive_summary(
            config, recommendation, edge_pct, greeks.get("early_exercise_premium", 0)
        )

        # Detailed analysis
        analysis = self._detailed_analysis(
            model_price, market_bid, market_mid, market_ask, edge_pct,
            greeks, risk_score, prob_profit, config
        )

        return StructurerOpinion(
            recommendation=recommendation,
            fair_value=model_price,
            market_bid=market_bid,
            market_mid=market_mid,
            market_ask=market_ask,
            edge_pct=edge_pct,
            risk_score=risk_score,
            probability_of_profit=prob_profit,
            greeks_assessment=greeks_assessment,
            moneyness_status=moneyness,
            recommended_action=action,
            hedge_recommendation=hedge_rec,
            executive_summary=exec_summary,
            detailed_analysis=analysis,
        )

    def _fetch_market_prices(self, ticker: str, strike: float, days: int) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        """Fetch bid/ask prices from Yahoo Finance.

        Returns:
            (bid, mid, ask) or (None, None, None) if fetch fails
        """
        try:
            import yfinance as yf
            stock = yf.Ticker(ticker)

            # Get option chain
            exp_date = stock.options[0]  # First available expiration
            chain = stock.option_chain(exp_date)

            # Find closest strike in puts
            puts = chain.puts
            closest_put = puts.iloc[(puts["strike"] - strike).abs().argsort()[:1]]

            if not closest_put.empty:
                bid = closest_put["bid"].values[0]
                ask = closest_put["ask"].values[0]
                mid = (bid + ask) / 2
                return float(bid), float(mid), float(ask)

        except Exception:
            pass

        return None, None, None

    def _calculate_edge(self, model_price: float, market_mid: Optional[float],
                       market_bid: Optional[float], market_ask: Optional[float]) -> float:
        """Calculate edge as percentage difference from market mid.

        Positive edge = our model price is higher (good for selling)
        Negative edge = our model price is lower (good for buying)
        """
        if market_mid is None:
            return 0.0

        edge = ((model_price - market_mid) / market_mid) * 100
        return float(edge)

    def _assess_moneyness(self, S: float, K: float, option_type: str) -> str:
        """Assess whether option is ITM, ATM, or OTM."""
        moneyness = S / K

        if option_type.endswith("call"):
            if moneyness > 1.03:
                return f"Deep ITM ({moneyness:.2%})"
            elif moneyness > 0.98:
                return f"ATM ({moneyness:.2%})"
            elif moneyness > 0.95:
                return f"Near OTM ({moneyness:.2%})"
            else:
                return f"Deep OTM ({moneyness:.2%})"
        else:  # put
            if moneyness < 0.97:
                return f"Deep ITM ({moneyness:.2%})"
            elif moneyness < 1.02:
                return f"ATM ({moneyness:.2%})"
            elif moneyness < 1.05:
                return f"Near OTM ({moneyness:.2%})"
            else:
                return f"Deep OTM ({moneyness:.2%})"

    def _assess_greeks(self, greeks: Dict, config) -> Dict[str, str]:
        """Interpret Greeks from a risk perspective."""
        assessments = {}

        delta = greeks.get("delta", 0)
        assessments["delta"] = self._delta_interpretation(delta, config.option_type)

        gamma = greeks.get("gamma", 0)
        assessments["gamma"] = self._gamma_interpretation(gamma, abs(delta))

        vega = greeks.get("vega", 0)
        assessments["vega"] = self._vega_interpretation(vega, config.volatility)

        theta = greeks.get("theta", 0)
        assessments["theta"] = self._theta_interpretation(theta, config.days_to_expiration)

        rho = greeks.get("rho", 0)
        assessments["rho"] = self._rho_interpretation(rho, config.option_type)

        return assessments

    def _delta_interpretation(self, delta: float, option_type: str) -> str:
        """Interpret delta value."""
        abs_delta = abs(delta)

        if abs_delta > 0.9:
            return f"Deep ITM behavior (delta={delta:.3f}). Lose ${abs(delta):.2f} per $1 move."
        elif abs_delta > 0.7:
            return f"High directional exposure (delta={delta:.3f}). Behaves mostly like stock."
        elif abs_delta > 0.4:
            return f"Balanced risk (delta={delta:.3f}). Meaningful directional exposure."
        elif abs_delta > 0.2:
            return f"Low delta (delta={delta:.3f}). Leverage play, high theta bleed."
        else:
            return f"Very low delta (delta={delta:.3f}). Pure volatility play, high theta decay."

    def _gamma_interpretation(self, gamma: float, abs_delta: float) -> str:
        """Interpret gamma value."""
        if gamma > 0.005:
            return f"High gamma (G={gamma:.6f}). Delta changes rapidly, needs frequent rebalancing."
        elif gamma > 0.001:
            return f"Moderate gamma (G={gamma:.6f}). Delta moves with stock price."
        elif gamma > 0.0001:
            return f"Low gamma (G={gamma:.6f}). Stable delta, easy to hedge."
        else:
            return f"Minimal gamma (G={gamma:.6f}). Delta essentially constant."

    def _vega_interpretation(self, vega: float, current_vol: float) -> str:
        """Interpret vega value."""
        if abs(vega) > 15:
            return f"High vega (V={vega:.2f}). Volatility bet dominates. 1% vol move = ${abs(vega):.2f} P&L."
        elif abs(vega) > 5:
            return f"Moderate vega (V={vega:.2f}). Meaningful vol exposure. 1% vol move = ${abs(vega):.2f} P&L."
        elif abs(vega) > 1:
            return f"Low vega (V={vega:.2f}). Vol moves have modest impact."
        else:
            return f"Minimal vega (V={vega:.2f}). Essentially vol-insensitive."

    def _theta_interpretation(self, theta: float, days: int) -> str:
        """Interpret theta (time decay)."""
        if theta > 0:
            return f"Positive theta (T=${theta:.2f}/day). Time decay works in your favor."
        elif theta > -1:
            return f"Minimal theta decay (T=${theta:.2f}/day). Time not a major factor."
        elif theta > -5:
            return f"Moderate theta (T=${theta:.2f}/day). Gradual time decay."
        else:
            return f"Fast theta decay (T=${theta:.2f}/day). Position loses value daily."

    def _rho_interpretation(self, rho: float, option_type: str) -> str:
        """Interpret rho (rate sensitivity)."""
        if abs(rho) > 10:
            return f"High rate sensitivity (R={rho:.2f}). Interest rate moves matter. 1% rate move = ${abs(rho):.2f} P&L."
        elif abs(rho) > 1:
            return f"Moderate rate sensitivity (R={rho:.2f}). 1% rate move = ${abs(rho):.2f} P&L."
        else:
            return f"Low rate sensitivity (R={rho:.2f}). Rates not a major driver."

    def _probability_of_profit(self, S: float, K: float, price: float, paths: Optional[np.ndarray],
                               option_type: str) -> float:
        """Estimate probability of profit if held to expiration."""
        if paths is None or len(paths) == 0:
            return 0.0

        final_prices = paths[:, -1]

        if option_type.endswith("call"):
            payoffs = np.maximum(final_prices - K, 0)
            # Profit if payoff > premium paid
            profit_paths = np.sum(payoffs > price)
        else:  # put
            payoffs = np.maximum(K - final_prices, 0)
            # Profit if payoff > premium paid
            profit_paths = np.sum(payoffs > price)

        prob_profit = (profit_paths / len(paths)) * 100
        return float(prob_profit)

    def _calculate_risk_score(self, greeks: Dict, config, std_error: float) -> int:
        """Calculate risk score from 1-10 (10 is highest risk)."""
        score = 0

        # Delta risk
        abs_delta = abs(greeks.get("delta", 0))
        if abs_delta > 0.7:
            score += 3
        elif abs_delta > 0.3:
            score += 2
        else:
            score += 1

        # Gamma risk
        gamma = greeks.get("gamma", 0)
        if gamma > 0.005:
            score += 2
        elif gamma > 0.001:
            score += 1

        # Vega risk
        vega = abs(greeks.get("vega", 0))
        if vega > 15:
            score += 2
        elif vega > 5:
            score += 1

        # Time to expiry risk
        if config.days_to_expiration < 7:
            score += 2
        elif config.days_to_expiration < 30:
            score += 1

        # Model uncertainty
        if std_error > 0 and config.days_to_expiration > 0:
            error_pct = (std_error / config.strike_price) * 100
            if error_pct > 1:
                score += 1

        return min(10, max(1, score))

    def _generate_recommendation(self, model_price: float, market_mid: Optional[float],
                                market_bid: Optional[float], market_ask: Optional[float],
                                edge_pct: float, risk_score: int, days: int,
                                moneyness: str, delta: float) -> Tuple[str, str]:
        """Generate trading recommendation."""
        if market_mid is None:
            return "HOLD", "Insufficient market data"

        # Strong signals
        if model_price < market_bid:
            return "STRONG_SELL", f"Trading {abs(edge_pct):.1f}% above fair value"

        if model_price > market_ask and edge_pct > 2.0 and risk_score <= 6:
            return "STRONG_BUY", f"Trading {edge_pct:.1f}% below fair value with good risk/reward"

        # Medium signals
        if edge_pct > 1.5 and risk_score <= 6:
            return "BUY", f"Fair value edge of {edge_pct:.1f}% offers margin of safety"

        if edge_pct < -1.5 and risk_score <= 6:
            return "SELL", f"Trading {abs(edge_pct):.1f}% above fair value"

        # Default
        return "HOLD", f"Fair valued relative to model (edge: {edge_pct:+.1f}%)"

    def _hedge_strategy(self, option_type: str, greeks: Dict, config) -> str:
        """Recommend hedging strategy."""
        delta = greeks.get("delta", 0)
        gamma = greeks.get("gamma", 0)
        vega = greeks.get("vega", 0)

        recommendations = []

        # Delta hedge
        if abs(delta) > 0.5:
            hedge_shares = abs(delta) * 100  # Per contract
            if option_type.endswith("put") and delta < 0:
                recommendations.append(f"Delta hedge: Buy {hedge_shares:.0f} shares per contract")
            elif option_type.endswith("call") and delta > 0:
                recommendations.append(f"Delta hedge: Short {hedge_shares:.0f} shares per contract")

        # Gamma risk
        if gamma > 0.003:
            recommendations.append("High gamma: Consider selling wider strikes or reduce position size to limit rebalancing costs")

        # Vega risk
        if abs(vega) > 10:
            if vega > 0:
                recommendations.append("Long vega: If vol drops below current, consider selling vol (put spreads)")
            else:
                recommendations.append("Short vega: If vol spikes above current, buy protective straddles")

        if not recommendations:
            recommendations.append("Position profile manageable. Standard delta hedging sufficient.")

        return " | ".join(recommendations)

    def _executive_summary(self, config, recommendation: str, edge_pct: float,
                          early_exercise_prem: float) -> str:
        """Generate one-paragraph executive summary."""
        option_name = config.option_type.replace("_", " ").title()
        action_word = {
            "STRONG_BUY": "significantly underpriced",
            "BUY": "moderately underpriced",
            "HOLD": "fairly valued",
            "SELL": "moderately overpriced",
            "STRONG_SELL": "significantly overpriced",
        }.get(recommendation, "fairly valued")

        summary = f"{option_name} on {config.underlying} is {action_word}. "

        if abs(edge_pct) > 1:
            summary += f"Fair value estimate suggests {abs(edge_pct):.1f}% edge vs market mid. "
        else:
            summary += "Market pricing appears to match theoretical value. "

        if early_exercise_prem > 0:
            summary += f"American optionality worth {early_exercise_prem:.1%} premium. "

        summary += f"Recommend {recommendation.replace('_', ' ')}."

        return summary

    def _detailed_analysis(self, model_price: float, market_bid: Optional[float],
                          market_mid: Optional[float], market_ask: Optional[float],
                          edge_pct: float, greeks: Dict, risk_score: int,
                          prob_profit: float, config) -> list:
        """Generate detailed bullet-point analysis."""
        analysis = []

        # Pricing
        analysis.append(f"FAIR VALUE: ${model_price:.4f}")
        if market_mid:
            analysis.append(f"Market price: ${market_bid:.2f} bid / ${market_mid:.2f} mid / ${market_ask:.2f} ask")
            if market_ask:
                spread = ((market_ask - market_bid) / market_mid) * 100
                analysis.append(f"Bid/ask spread: {spread:.2f}%")

        # Edge
        if market_mid:
            analysis.append(f"Edge vs market: {edge_pct:+.2f}% ({'Buy if < ' + f'${market_mid * (1 + edge_pct/100):.2f}' if edge_pct < 0 else 'Sell if > ' + f'${market_mid * (1 + edge_pct/100):.2f}'})")

        # Greeks snapshot
        analysis.append("")
        analysis.append("GREEKS SNAPSHOT:")
        for greek, value in [("Delta", greeks.get("delta")), ("Gamma", greeks.get("gamma")),
                            ("Vega", greeks.get("vega")), ("Theta", greeks.get("theta")),
                            ("Rho", greeks.get("rho"))]:
            if value is not None:
                analysis.append(f"  {greek}: {value:+.4f}")

        # Risk profile
        analysis.append("")
        analysis.append(f"RISK SCORE: {risk_score}/10 ({'HIGH RISK' if risk_score >= 7 else 'MODERATE' if risk_score >= 4 else 'LOW RISK'})")
        analysis.append(f"Probability of profit: {prob_profit:.1f}%")
        analysis.append(f"Moneyness: {config.spot_price:.2f} / {config.strike_price:.2f} = {config.spot_price/config.strike_price:.2%}")
        analysis.append(f"Days to expiration: {config.days_to_expiration}")

        return analysis
