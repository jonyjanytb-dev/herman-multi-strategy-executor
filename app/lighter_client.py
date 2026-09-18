from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Optional

import requests

from .models import Candle
from .timeframes import lighter_interval_seconds

LIGHTER_PROFILES: dict[str, tuple[str, int]] = {
    "mainnet": ("https://mainnet.zklighter.elliot.ai", 304),
    "robinhood": ("https://api.rh.lighter.xyz", 466324),
    "testnet": ("https://testnet.zklighter.elliot.ai", 300),
    "robinhood_testnet": ("https://api.rh-testnet.lighter.xyz", 300),
}


@dataclass(frozen=True)
class LighterMarket:
    symbol: str
    market_id: int
    size_decimals: int
    price_decimals: int
    min_base_amount: Decimal
    min_quote_amount: Decimal
    mark_price: Optional[Decimal] = None


def profile_api_url(profile: str) -> str:
    try:
        return LIGHTER_PROFILES[profile][0]
    except KeyError as exc:
        raise ValueError(f"Unknown Lighter profile: {profile}") from exc


def profile_chain_id(profile: str) -> int:
    try:
        return LIGHTER_PROFILES[profile][1]
    except KeyError as exc:
        raise ValueError(f"Unknown Lighter profile: {profile}") from exc


def price_to_int(price: float | Decimal, decimals: int) -> int:
    scale = Decimal(10) ** decimals
    return int((Decimal(str(price)) * scale).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def size_to_int(size: float | Decimal, decimals: int) -> int:
    scale = Decimal(10) ** decimals
    return int((Decimal(str(size)) * scale).quantize(Decimal("1"), rounding=ROUND_DOWN))


class LighterPublicClient:
    """Small synchronous wrapper around Lighter's public REST API.

    Trading writes use the official lighter-sdk signer. Keeping market/account
    reads here avoids coupling the synchronous bot loop to the SDK's asyncio
    client and makes candle pagination deterministic.
    """

    def __init__(
        self,
        profile: str = "mainnet",
        symbol: str = "BTC",
        timeout: float = 15.0,
        session: Optional[requests.Session] = None,
    ):
        self.profile = profile
        self.base_url = profile_api_url(profile)
        self.symbol = symbol.upper()
        self.timeout = timeout
        self.session = session or requests.Session()
        self._market: Optional[LighterMarket] = None

    def _get(self, path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        response = self.session.get(self.base_url + path, params=params or {}, timeout=self.timeout)
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError(f"Lighter returned unexpected JSON for {path}")
        code = payload.get("code")
        if code not in (None, 0, 200):
            raise RuntimeError(f"Lighter API error {code}: {payload.get('message') or payload}")
        return payload

    def resolve_market(self, refresh: bool = False) -> LighterMarket:
        if self._market is not None and not refresh:
            return self._market
        payload = self._get("/api/v1/orderBookDetails", {"filter": "perp"})
        rows = payload.get("order_book_details") or []
        for row in rows:
            if str(row.get("symbol", "")).upper() != self.symbol:
                continue
            size_decimals = int(row.get("size_decimals", row.get("supported_size_decimals", 0)))
            price_decimals = int(row.get("price_decimals", row.get("supported_price_decimals", 0)))
            self._market = LighterMarket(
                symbol=self.symbol,
                market_id=int(row["market_id"]),
                size_decimals=size_decimals,
                price_decimals=price_decimals,
                min_base_amount=Decimal(str(row.get("min_base_amount") or "0")),
                min_quote_amount=Decimal(str(row.get("min_quote_amount") or "0")),
                mark_price=Decimal(str(row["mark_price"])) if row.get("mark_price") not in (None, "") else None,
            )
            return self._market
        raise RuntimeError(f"Lighter perp market not found: {self.symbol} ({self.profile})")

    @staticmethod
    def _candle_timestamp_ms(raw: Any) -> int:
        value = int(raw)
        return value * 1000 if value < 10_000_000_000 else value

    @classmethod
    def _parse_candles(cls, rows: list[dict[str, Any]]) -> list[Candle]:
        parsed: list[Candle] = []
        for row in rows:
            try:
                parsed.append(
                    Candle(
                        cls._candle_timestamp_ms(row["t"]),
                        float(row["o"]),
                        float(row["h"]),
                        float(row["l"]),
                        float(row["c"]),
                        float(row.get("v") or 0.0),
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(parsed, key=lambda x: x.t)

    def candles(self, count: int, resolution: str = "1m", now_ms: Optional[int] = None) -> list[Candle]:
        if count <= 0:
            return []
        interval_seconds = lighter_interval_seconds(resolution)
        interval_ms = interval_seconds * 1000

        market = self.resolve_market()
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        now_sec = now_ms // 1000

        # Lighter returns at most 500 candles per request. Walk fixed time
        # windows forward and de-duplicate timestamps so Strategy 1.2 can
        # reliably obtain its ~3000-bar replay window.
        start_sec = now_sec - (count + 10) * interval_seconds
        cursor = start_sec
        merged: dict[int, Candle] = {}
        while cursor < now_sec and len(merged) < count + 5:
            end_sec = min(now_sec, cursor + 500 * interval_seconds)
            payload = self._get(
                "/api/v1/candles",
                {
                    "market_id": market.market_id,
                    "resolution": resolution,
                    "start_timestamp": cursor,
                    "end_timestamp": end_sec,
                    "count_back": 500,
                },
            )
            rows = payload.get("c") or []
            for candle in self._parse_candles(rows):
                if candle.t + interval_ms <= now_ms:
                    merged[candle.t] = candle
            cursor = end_sec + 1

        return sorted(merged.values(), key=lambda x: x.t)[-count:]

    def account(self, account_index: int, active_only: bool = True) -> dict[str, Any]:
        payload = self._get(
            "/api/v1/account",
            {
                "by": "index",
                "value": str(int(account_index)),
                "active_only": "true" if active_only else "false",
            },
        )
        accounts = payload.get("accounts") or []
        if not accounts:
            raise RuntimeError(f"Lighter account not found: {account_index}")
        return dict(accounts[0])

    def mid_price(self) -> float:
        market = self.resolve_market(refresh=True)
        if market.mark_price is not None and market.mark_price > 0:
            return float(market.mark_price)
        raise RuntimeError(f"Lighter mark price unavailable for {self.symbol}")
