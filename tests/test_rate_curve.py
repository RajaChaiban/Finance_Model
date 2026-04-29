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
