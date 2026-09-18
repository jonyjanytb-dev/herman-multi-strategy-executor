from __future__ import annotations

import pytest

from app.bot import build_market_data
from app.config import Config
from app.lighter_client import LighterPublicClient, price_to_int, size_to_int
from app.market_data import LighterMarketData


def base_env(monkeypatch):
    monkeypatch.setenv("STRATEGY", "aw_liquidity")
    monkeypatch.setenv("EXCHANGE", "lighter")
    monkeypatch.setenv("DRY_RUN", "true")
    monkeypatch.setenv("INTERVAL", "1m")
    monkeypatch.setenv("LIGHTER_PROFILE", "mainnet")
    monkeypatch.setenv("LIGHTER_SYMBOL", "BTC")
    monkeypatch.setenv("STATE_PATH", "")


def test_lighter_dry_run_config_does_not_require_secret(monkeypatch):
    base_env(monkeypatch)
    cfg = Config.load()
    assert cfg.exchange == "lighter"
    assert cfg.market_symbol == "mainnet:BTC"
    assert cfg.state_path.endswith("state-lighter-aw_liquidity.json")
    assert isinstance(build_market_data(cfg), LighterMarketData)


def test_lighter_live_requires_dedicated_api_credentials(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("DRY_RUN", "false")
    with pytest.raises(ValueError, match="LIGHTER_ACCOUNT_INDEX"):
        Config.load()

    monkeypatch.setenv("LIGHTER_ACCOUNT_INDEX", "123")
    monkeypatch.setenv("LIGHTER_API_KEY_INDEX", "3")
    monkeypatch.setenv("LIGHTER_API_KEY_PRIVATE", "dedicated-lighter-api-key")
    cfg = Config.load()
    assert cfg.lighter_account_index == 123
    assert cfg.lighter_api_key_index == 3


def test_lighter_integer_scaling_helpers():
    assert price_to_int(80970.1, 1) == 809701
    assert size_to_int(0.123456, 5) == 12345


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self):
        self.calls = []

    def get(self, url, params=None, timeout=None):
        params = dict(params or {})
        self.calls.append((url, params, timeout))
        if url.endswith("/api/v1/orderBookDetails"):
            return FakeResponse({
                "code": 0,
                "order_book_details": [{
                    "symbol": "BTC",
                    "market_id": 1,
                    "market_type": "perp",
                    "size_decimals": 5,
                    "price_decimals": 1,
                    "min_base_amount": "0.00001",
                    "min_quote_amount": "1",
                    "mark_price": "80970.0",
                }],
            })
        if url.endswith("/api/v1/candles"):
            return FakeResponse({
                "code": 0,
                "c": [
                    {"t": 1700000040, "o": 100, "h": 102, "l": 99, "c": 101, "v": 2},
                    {"t": 1700000100, "o": 101, "h": 103, "l": 100, "c": 102, "v": 3},
                ],
            })
        if url.endswith("/api/v1/account"):
            return FakeResponse({
                "code": 0,
                "accounts": [{
                    "index": 123,
                    "available_balance": "500",
                    "collateral": "500",
                    "positions": [{
                        "market_id": 1,
                        "sign": 1,
                        "position": "0.01",
                        "avg_entry_price": "80000",
                    }],
                }],
            })
        raise AssertionError(f"unexpected URL {url}")


def test_lighter_public_market_candles_and_account():
    session = FakeSession()
    client = LighterPublicClient(profile="mainnet", symbol="BTC", session=session)
    market = client.resolve_market()
    assert market.market_id == 1
    assert market.size_decimals == 5
    assert market.price_decimals == 1

    candles = client.candles(count=2, now_ms=1_700_000_180_000)
    assert [c.c for c in candles] == [101.0, 102.0]
    assert candles[0].t == 1_700_000_040_000

    account = client.account(123)
    assert account["available_balance"] == "500"
    assert account["positions"][0]["avg_entry_price"] == "80000"
