from datetime import datetime, timedelta, timezone

from robinhood_sniper.models import PricePoint
from robinhood_sniper.structure import detect_higher_low


def _points(prices: list[float]) -> list[PricePoint]:
    start = datetime.now(timezone.utc) - timedelta(minutes=len(prices))
    return [PricePoint(start + timedelta(minutes=i), p) for i, p in enumerate(prices)]


def test_classic_higher_low_is_detected():
    # launch -> pump -> 30% retrace -> higher low held -> breakout attempt
    prices = [1.0, 2.0, 1.4, 1.45, 1.6]
    result = detect_higher_low(_points(prices))
    assert result.has_higher_low is True
    assert 25 <= result.retracement_pct <= 35


def test_straight_fomo_candle_is_rejected_when_retrace_too_shallow():
    prices = [1.0, 4.0, 3.9, 3.85, 3.9]
    result = detect_higher_low(_points(prices))
    assert result.has_higher_low is False
    assert "shallow" in result.notes


def test_full_giveback_is_a_lower_low_not_higher_low():
    prices = [1.0, 2.0, 0.9, 0.95, 1.0]
    result = detect_higher_low(_points(prices))
    assert result.has_higher_low is False


def test_insufficient_history_is_handled():
    result = detect_higher_low(_points([1.0, 1.2]))
    assert result.has_higher_low is False
    assert result.retracement_pct is None
