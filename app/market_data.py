from __future__ import annotations

import logging
import time
from typing import Callable, Optional

import requests

from .lighter_client import LighterPublicClient
from .models import Candle
from .okx_client import OKXClient
from .timeframes import lighter_interval_seconds

log = logging.getLogger(__name__)

MAINNET_INFO = "https://api.hyperliquid.xyz/info"
TESTNET_INFO = "https://api.hyperliquid-testnet.xyz/info"
MINUTE_MS = 60_000


class HyperliquidMarketData:
    def __init__(self, network: str, coin: str, interval: str = "1m", timeout: float = 15.0):
        self.url = MAINNET_INFO if network == "mainnet" else TESTNET_INFO
        self.coin = coin
        self.interval = interval
        self.timeout = timeout
        self.session = requests.Session()
        self._cache: list[Candle] = []
        self._last_success_minute: Optional[int] = None
        self._retry_after_ms = 0

    def _request(self, start: int, end: int) -> list[Candle]:
        payload = {"type": "candleSnapshot", "req": {"coin": self.coin, "interval": self.interval, "startTime": start, "endTime": end}}
        r = self.session.post(self.url, json=payload, timeout=self.timeout)
        if r.status_code == 429:
            retry_after = r.headers.get("Retry-After", "")
            try:
                retry_seconds = max(30.0, float(retry_after))
            except (TypeError, ValueError):
                retry_seconds = 60.0
            self._retry_after_ms = int(time.time() * 1000 + retry_seconds * 1000)
            raise RuntimeError(f"Hyperliquid REST rate limited (429); cooling down for {retry_seconds:.0f}s")
        r.raise_for_status()
        out = [Candle(int(x["t"]), float(x["o"]), float(x["h"]), float(x["l"]), float(x["c"]), float(x.get("v", 0.0))) for x in r.json()]
        return sorted(out, key=lambda x: x.t)

    @staticmethod
    def _closed(candles: list[Candle], now: int) -> list[Candle]:
        return [c for c in candles if c.t + MINUTE_MS <= now]

    def _bootstrap(self, count: int, now: int) -> list[Candle]:
        windows = [8 * 60 * MINUTE_MS, 24 * 60 * MINUTE_MS, 72 * 60 * MINUTE_MS, 7 * 24 * 60 * MINUTE_MS]
        best: list[Candle] = []
        for i, window in enumerate(windows):
            candles = self._closed(self._request(now - window, now), now)
            if len(candles) > len(best):
                best = candles
            if len(best) >= count:
                break
            if i < len(windows) - 1:
                time.sleep(1.0)
        return best[-count:]

    def _incremental(self, count: int, now: int) -> list[Candle]:
        start = max(self._cache[-1].t - 3 * MINUTE_MS, now - 10 * MINUTE_MS)
        fresh = self._closed(self._request(start, now), now)
        merged = {c.t: c for c in self._cache}
        for c in fresh:
            merged[c.t] = c
        return sorted(merged.values(), key=lambda x: x.t)[-count:]

    def fetch_recent(self, count: int = 500) -> list[Candle]:
        now = int(time.time() * 1000)
        minute_bucket = now // MINUTE_MS
        if now < self._retry_after_ms:
            if self._cache:
                return self._cache[-count:]
            raise RuntimeError("Hyperliquid REST cooldown active")
        if self._cache and self._last_success_minute == minute_bucket:
            return self._cache[-count:]
        try:
            cache = self._bootstrap(count, now) if not self._cache else self._incremental(count, now)
        except RuntimeError as exc:
            if "429" in str(exc) and self._cache:
                log.warning("%s; using cached candles", exc)
                return self._cache[-count:]
            raise
        if cache:
            self._cache = cache
            self._last_success_minute = minute_bucket
            self._retry_after_ms = 0
        return self._cache[-count:]


class OKXMarketData:
    def __init__(self, inst_id: str, interval: str = "1m", base_url: str = "https://www.okx.com", client: Optional[OKXClient] = None, clock_ms: Optional[Callable[[], int]] = None):
        self.inst_id = inst_id
        self.interval = interval
        self.client = client or OKXClient(base_url=base_url)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._cache: list[Candle] = []
        self._last_success_minute: Optional[int] = None

    def _request(self, limit: int) -> list[Candle]:
        rows = self.client.get_public("/api/v5/market/candles", {"instId": self.inst_id, "bar": self.interval, "limit": str(limit)})
        out: list[Candle] = []
        for row in rows:
            if len(row) < 9 or str(row[8]) != "1":
                continue
            out.append(Candle(int(row[0]), float(row[1]), float(row[2]), float(row[3]), float(row[4]), float(row[5])))
        return sorted(out, key=lambda x: x.t)

    def fetch_recent(self, count: int = 500) -> list[Candle]:
        now = self._clock_ms()
        bucket = now // MINUTE_MS
        if self._cache and self._last_success_minute == bucket:
            return self._cache[-count:]
        fresh = self._request(min(300, count if not self._cache else max(20, min(count, 100))))
        merged = {c.t: c for c in self._cache}
        for c in fresh:
            merged[c.t] = c
        self._cache = sorted(merged.values(), key=lambda x: x.t)[-count:]
        expected = (bucket - 1) * MINUTE_MS
        if self._cache and self._cache[-1].t >= expected:
            self._last_success_minute = bucket
        return self._cache[-count:]


class LighterMarketData:
    def __init__(self, profile: str, symbol: str, interval: str = "1m", client: Optional[LighterPublicClient] = None):
        self._interval_ms = lighter_interval_seconds(interval) * 1000
        self.profile = profile
        self.symbol = symbol.upper()
        self.interval = interval
        self.client = client or LighterPublicClient(profile=profile, symbol=self.symbol)
        self._cache: list[Candle] = []
        self._last_success_minute: Optional[int] = None

    def fetch_recent(self, count: int = 500) -> list[Candle]:
        now = int(time.time() * 1000)
        bucket = now // self._interval_ms
        if self._cache and self._last_success_minute == bucket and len(self._cache) >= count:
            return self._cache[-count:]

        # Strategy 1.2 may request ~3000 bars. Lighter's public API is paged
        # internally by LighterPublicClient in 500-candle windows.
        fresh = self.client.candles(count=count, resolution=self.interval, now_ms=now)
        if fresh:
            self._cache = fresh[-count:]
            self._last_success_minute = bucket
        return self._cache[-count:]
