from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Optional

from .config import Config
from .lighter_client import LighterPublicClient, price_to_int, profile_api_url, profile_chain_id, size_to_int
from .okx_client import OKXAPIError, OKXClient

log = logging.getLogger(__name__)
PERP_MAX_DECIMALS = 6
PRICE_SIGNIFICANT_FIGURES = 5
LIGHTER_MAX_CLIENT_ORDER_INDEX = (1 << 48) - 1


@dataclass(frozen=True)
class Position:
    size: float
    entry_px: Optional[float]

    @property
    def flat(self) -> bool:
        return abs(self.size) < 1e-15


class BaseExecutor:
    def position(self) -> Position: raise NotImplementedError
    def open_market(self, is_buy: bool, notional_usdc: float) -> dict: raise NotImplementedError
    def close_market(self) -> dict: raise NotImplementedError
    def place_protection(self, position_size: float, tp: float, sl: float): raise NotImplementedError
    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float): raise NotImplementedError
    def cancel_oid(self, oid: Optional[int]) -> None: raise NotImplementedError
    def cancel_all_protection(self) -> None: raise NotImplementedError
    def recover_protection(self): return None, None, None, None


class DryRunExecutor(BaseExecutor):
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._position = Position(0.0, None)
        self._oid = 1000
        self._tp_oid = self._sl_oid = None
        self.last_mid = 25_000.0

    def set_mid(self, px: float) -> None: self.last_mid = px
    def position(self) -> Position: return self._position

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        size = notional_usdc / self.last_mid
        self._position = Position(size if is_buy else -size, self.last_mid)
        return {"status": "ok", "dry_run": True}

    def close_market(self) -> dict:
        self._position = Position(0.0, None)
        self.cancel_all_protection()
        return {"status": "ok", "dry_run": True, "closed": True}

    def _next(self) -> int:
        self._oid += 1
        return self._oid

    def place_protection(self, position_size: float, tp: float, sl: float):
        self._sl_oid, self._tp_oid = self._next(), self._next()
        log.info("DRY RUN protection SL=%.4f TP=%.4f", sl, tp)
        return self._tp_oid, self._sl_oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        self._tp_oid = old_tp_oid or self._next()
        return self._tp_oid

    def cancel_oid(self, oid: Optional[int]) -> None: return
    def cancel_all_protection(self) -> None: self._tp_oid = self._sl_oid = None


class HyperliquidExecutor(BaseExecutor):
    def __init__(self, cfg: Config):
        from eth_account import Account
        from hyperliquid.exchange import Exchange
        from hyperliquid.info import Info
        from hyperliquid.utils import constants

        self.cfg = cfg
        base_url = constants.MAINNET_API_URL if cfg.network == "mainnet" else constants.TESTNET_API_URL
        wallet = Account.from_key(cfg.api_private_key)
        self.info = Info(base_url, skip_ws=True, perp_dexs=[cfg.dex])
        self.exchange = Exchange(wallet, base_url, account_address=cfg.account_address, perp_dexs=[cfg.dex])
        self.market_meta = self._validate_market()
        self.sz_decimals = int(self.market_meta["szDecimals"])
        if cfg.leverage > 0:
            max_lev = int(self.market_meta.get("maxLeverage", cfg.leverage))
            if cfg.leverage > max_lev:
                raise RuntimeError(f"LEVERAGE={cfg.leverage} exceeds market maxLeverage={max_lev}")
            mode = str(self.market_meta.get("marginMode", ""))
            only_isolated = bool(self.market_meta.get("onlyIsolated", False)) or mode in {"strictIsolated", "noCross"}
            self.exchange.update_leverage(cfg.leverage, cfg.coin, is_cross=not only_isolated)

    def _validate_market(self) -> dict:
        meta = self.info.meta(dex=self.cfg.dex)
        for item in meta.get("universe", []):
            if item.get("name") == self.cfg.coin:
                return item
        raise RuntimeError(f"{self.cfg.coin} not found in HIP-3 dex {self.cfg.dex}")

    def _round_size(self, size: float) -> float:
        q = Decimal("1").scaleb(-self.sz_decimals)
        return float(Decimal(str(size)).quantize(q, rounding=ROUND_DOWN))

    def _round_price(self, price: float) -> float:
        significant = float(f"{price:.{PRICE_SIGNIFICANT_FIGURES}g}")
        return round(significant, PERP_MAX_DECIMALS - self.sz_decimals)

    def mid(self) -> float:
        return float(self.info.all_mids(self.cfg.dex)[self.cfg.coin])

    def position(self) -> Position:
        state = self.info.user_state(self.cfg.account_address, self.cfg.dex)
        for wrapper in state.get("assetPositions", []):
            p = wrapper.get("position", {})
            if p.get("coin") == self.cfg.coin:
                size = float(p.get("szi", 0.0))
                entry = p.get("entryPx")
                return Position(size, float(entry) if entry not in (None, "") else None)
        return Position(0.0, None)

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        size = self._round_size(notional_usdc / self.mid())
        if size <= 0:
            raise RuntimeError("Calculated order size rounded to zero")
        return self.exchange.market_open(self.cfg.coin, is_buy, size, slippage=self.cfg.max_slippage)

    def close_market(self) -> dict:
        pos = self.position()
        if pos.flat:
            return {"status": "ok", "already_flat": True}
        return self.exchange.market_close(self.cfg.coin, sz=self._round_size(abs(pos.size)), slippage=self.cfg.max_slippage)

    @staticmethod
    def _extract_oid(resp: dict) -> Optional[int]:
        try:
            status = resp["response"]["data"]["statuses"][0]
            if "resting" in status: return int(status["resting"]["oid"])
        except Exception: pass
        return None

    @staticmethod
    def _extract_error(resp: dict) -> Optional[str]:
        try:
            status = resp["response"]["data"]["statuses"][0]
            if "error" in status: return str(status["error"])
        except Exception: pass
        return None

    def _trigger(self, position_size: float, px: float, kind: str) -> int:
        is_buy = position_size < 0
        size = self._round_size(abs(position_size))
        trigger_px = self._round_price(px)
        typ = {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": kind}}
        resp = self.exchange.order(self.cfg.coin, is_buy, size, trigger_px, typ, reduce_only=True)
        err = self._extract_error(resp)
        if err: raise RuntimeError(f"Failed to create {kind.upper()}: {err}")
        oid = self._extract_oid(resp)
        if oid is None: raise RuntimeError(f"Failed to create {kind.upper()}: {resp}")
        return oid

    def place_protection(self, position_size: float, tp: float, sl: float):
        sl_oid = self._trigger(position_size, sl, "sl")
        try:
            tp_oid = self._trigger(position_size, tp, "tp")
        except Exception:
            log.exception("TP placement failed; keeping SL oid=%s", sl_oid)
            raise
        return tp_oid, sl_oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        if old_tp_oid is None:
            return self._trigger(position_size, tp, "tp")
        is_buy = position_size < 0
        size = self._round_size(abs(position_size))
        px = self._round_price(tp)
        typ = {"trigger": {"triggerPx": px, "isMarket": True, "tpsl": "tp"}}
        try:
            resp = self.exchange.modify_order(int(old_tp_oid), self.cfg.coin, is_buy, size, px, typ, reduce_only=True)
            if self._extract_error(resp): raise RuntimeError(self._extract_error(resp))
            return self._extract_oid(resp) or int(old_tp_oid)
        except Exception:
            new_oid = self._trigger(position_size, tp, "tp")
            self.cancel_oid(old_tp_oid)
            return new_oid

    def cancel_oid(self, oid: Optional[int]) -> None:
        if oid is None: return
        try: self.exchange.cancel(self.cfg.coin, int(oid))
        except Exception as exc: log.warning("cancel oid=%s failed: %s", oid, exc)

    def cancel_all_protection(self) -> None:
        for order in self.info.frontend_open_orders(self.cfg.account_address, self.cfg.dex):
            if order.get("coin") == self.cfg.coin and order.get("reduceOnly") and order.get("isTrigger"):
                self.cancel_oid(order.get("oid"))

    def recover_protection(self):
        tp_oid = tp_px = sl_oid = sl_px = None
        for order in self.info.frontend_open_orders(self.cfg.account_address, self.cfg.dex):
            if order.get("coin") != self.cfg.coin or not order.get("reduceOnly") or not order.get("isTrigger"): continue
            typ = str(order.get("orderType", "")); trigger = order.get("triggerPx")
            if "Take Profit" in typ: tp_oid, tp_px = int(order["oid"]), float(trigger)
            elif "Stop" in typ: sl_oid, sl_px = int(order["oid"]), float(trigger)
        return tp_oid, tp_px, sl_oid, sl_px




class LighterExecutor(BaseExecutor):
    """Lighter Core / Robinhood Chain execution through the official SDK."""

    def __init__(self, cfg: Config):
        import lighter

        self.cfg = cfg
        self.lighter = lighter
        self.public = LighterPublicClient(profile=cfg.lighter_profile, symbol=cfg.lighter_symbol)
        self.market = self.public.resolve_market()
        self.market_id = self.market.market_id
        self.account_index = int(cfg.lighter_account_index)
        self.api_key_index = int(cfg.lighter_api_key_index)

        # The official SDK is asyncio-based. The Herman bot is deliberately
        # synchronous, so keep one dedicated event loop for all signed writes
        # and private order reads rather than creating a new loop per request.
        self.loop = asyncio.new_event_loop()
        self.client, self.order_api = self._run(self._build_sdk_client())
        err = self.client.check_client()
        if err is not None:
            raise RuntimeError(f"Lighter API key validation failed: {err}")

        # Lighter signs ClientOrderIndex as an unsigned 48-bit value. A Unix
        # microsecond timestamp already exceeds that range; milliseconds stay
        # unique enough for this synchronous executor and fit for millennia.
        self._client_order_index = int(time.time() * 1_000)
        if cfg.leverage > 0:
            _, _, err = self._run(
                self.client.update_leverage(
                    market_index=self.market_id,
                    leverage=cfg.leverage,
                    margin_mode=self.client.CROSS_MARGIN_MODE,
                    api_key_index=self.api_key_index,
                )
            )
            if err is not None:
                raise RuntimeError(f"Lighter leverage update failed: {err}")

    async def _build_sdk_client(self):
        # aiohttp.ClientSession is created inside lighter.SignerClient, and
        # modern aiohttp requires construction while an event loop is running.
        # Official Lighter examples also build the client from async code.
        client = self.lighter.SignerClient(
            url=profile_api_url(self.cfg.lighter_profile),
            account_index=self.account_index,
            api_private_keys={self.api_key_index: self.cfg.lighter_api_key_private},
            chain_id=profile_chain_id(self.cfg.lighter_profile),
        )
        return client, client.order_api

    def _run(self, awaitable):
        return self.loop.run_until_complete(awaitable)

    def _next_client_order_index(self) -> int:
        if self._client_order_index >= LIGHTER_MAX_CLIENT_ORDER_INDEX:
            raise RuntimeError("Lighter client order index range exhausted")
        self._client_order_index += 1
        return self._client_order_index

    def _auth(self) -> str:
        token, err = self.client.create_auth_token_with_expiry(api_key_index=self.api_key_index)
        if err is not None or not token:
            raise RuntimeError(f"Lighter auth token failed: {err or 'empty token'}")
        return token

    def _active_orders(self):
        auth = self._auth()
        response = self._run(
            self.order_api.account_active_orders(
                authorization=auth,
                account_index=self.account_index,
                market_id=self.market_id,
                market_type="perp",
            )
        )
        return list(getattr(response, "orders", None) or [])

    def _find_order_index(self, client_order_index: int, timeout: float = 3.0) -> int:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            auth = self._auth()
            response = self._run(
                self.order_api.account_orders(
                    authorization=auth,
                    client_order_indexes=str(client_order_index),
                    account_index=self.account_index,
                )
            )
            for order in list(getattr(response, "orders", None) or []):
                if int(getattr(order, "client_order_index", -1)) == int(client_order_index):
                    return int(order.order_index)
            time.sleep(0.15)
        raise RuntimeError(f"Lighter order index not visible for client_order_index={client_order_index}")

    @staticmethod
    def _write_result(tx, response, err, action: str) -> dict:
        if err is not None:
            raise RuntimeError(f"Lighter {action} failed: {err}")
        return {
            "status": "ok",
            "exchange": "lighter",
            "action": action,
            "tx": str(tx),
            "response": str(response),
        }

    def position(self) -> Position:
        account = self.public.account(self.account_index, active_only=True)
        for row in account.get("positions") or []:
            if int(row.get("market_id", -1)) != self.market_id:
                continue
            quantity = abs(float(row.get("position") or 0.0))
            sign = int(row.get("sign") or 0)
            if quantity <= 0 or sign == 0:
                return Position(0.0, None)
            entry = row.get("avg_entry_price")
            return Position(quantity if sign > 0 else -quantity, float(entry) if entry not in (None, "") else None)
        return Position(0.0, None)

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        client_index = self._next_client_order_index()
        tx, response, err = self._run(
            self.client.create_market_order_quote_amount(
                market_index=self.market_id,
                client_order_index=client_index,
                quote_amount=float(notional_usdc),
                max_slippage=self.cfg.max_slippage,
                is_ask=not is_buy,
                reduce_only=False,
                api_key_index=self.api_key_index,
            )
        )
        return self._write_result(tx, response, err, "open_market")

    def close_market(self) -> dict:
        pos = self.position()
        if pos.flat:
            return {"status": "ok", "exchange": "lighter", "already_flat": True}
        base_amount = size_to_int(abs(pos.size), self.market.size_decimals)
        if base_amount <= 0:
            raise RuntimeError("Lighter close size rounded to zero")
        client_index = self._next_client_order_index()
        tx, response, err = self._run(
            self.client.create_market_order_limited_slippage(
                market_index=self.market_id,
                client_order_index=client_index,
                base_amount=base_amount,
                max_slippage=self.cfg.max_slippage,
                is_ask=pos.size > 0,
                reduce_only=True,
                api_key_index=self.api_key_index,
            )
        )
        return self._write_result(tx, response, err, "close_market")

    def _trigger(self, position_size: float, trigger_price: float, kind: str) -> int:
        is_ask = position_size > 0
        base_amount = size_to_int(abs(position_size), self.market.size_decimals)
        if base_amount <= 0:
            raise RuntimeError("Lighter protection size rounded to zero")

        trigger_int = price_to_int(trigger_price, self.market.price_decimals)
        slip = Decimal(str(self.cfg.max_slippage))
        px = Decimal(str(trigger_price)) * (Decimal("1") - slip if is_ask else Decimal("1") + slip)
        price_int = price_to_int(px, self.market.price_decimals)
        client_index = self._next_client_order_index()

        fn = self.client.create_sl_order if kind == "sl" else self.client.create_tp_order
        tx, response, err = self._run(
            fn(
                market_index=self.market_id,
                client_order_index=client_index,
                base_amount=base_amount,
                trigger_price=trigger_int,
                price=price_int,
                is_ask=is_ask,
                reduce_only=True,
                api_key_index=self.api_key_index,
            )
        )
        self._write_result(tx, response, err, f"create_{kind}")
        return self._find_order_index(client_index)

    def place_protection(self, position_size: float, tp: float, sl: float):
        # Stop first: if TP creation fails, the position still has native
        # downside protection and the next bot iteration can recover it.
        sl_oid = self._trigger(position_size, sl, "sl")
        try:
            tp_oid = self._trigger(position_size, tp, "tp")
        except Exception:
            log.exception("Lighter TP placement failed; keeping SL order_index=%s", sl_oid)
            raise
        return tp_oid, sl_oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        # Create replacement first so a transient cancel failure never leaves
        # the live position without a TP.
        new_oid = self._trigger(position_size, tp, "tp")
        if old_tp_oid is not None and int(old_tp_oid) != int(new_oid):
            self.cancel_oid(old_tp_oid)
        return new_oid

    def cancel_oid(self, oid: Optional[int]) -> None:
        if oid is None:
            return
        try:
            tx, response, err = self._run(
                self.client.cancel_order(
                    market_index=self.market_id,
                    order_index=int(oid),
                    api_key_index=self.api_key_index,
                )
            )
            self._write_result(tx, response, err, "cancel_order")
        except Exception as exc:
            log.warning("Lighter cancel order_index=%s failed: %s", oid, exc)

    def cancel_all_protection(self) -> None:
        for order in self._active_orders():
            if not bool(getattr(order, "reduce_only", False)):
                continue
            typ = str(getattr(order, "type", ""))
            if typ in {"stop-loss", "stop-loss-limit", "take-profit", "take-profit-limit"}:
                self.cancel_oid(int(order.order_index))

    def recover_protection(self):
        tp_oid = tp_px = sl_oid = sl_px = None
        for order in self._active_orders():
            if not bool(getattr(order, "reduce_only", False)):
                continue
            typ = str(getattr(order, "type", ""))
            trigger = getattr(order, "trigger_price", None)
            if trigger in (None, ""):
                continue
            if typ in {"take-profit", "take-profit-limit"} and tp_px is None:
                tp_oid, tp_px = int(order.order_index), float(trigger)
            elif typ in {"stop-loss", "stop-loss-limit"} and sl_px is None:
                sl_oid, sl_px = int(order.order_index), float(trigger)
        return tp_oid, tp_px, sl_oid, sl_px



class OKXExecutor(BaseExecutor):
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client = OKXClient(cfg.okx_base_url, cfg.okx_api_key, cfg.okx_secret_key, cfg.okx_passphrase, cfg.okx_demo)
        self.market_meta = self._validate_market()
        self.ct_val = Decimal(str(self.market_meta["ctVal"]))
        self.lot_size = Decimal(str(self.market_meta["lotSz"]))
        self.min_size = Decimal(str(self.market_meta["minSz"]))
        self.tick_size = Decimal(str(self.market_meta["tickSz"]))
        rows = self.client.get_private("/api/v5/account/config")
        self.position_mode = str(rows[0].get("posMode", "net_mode")) if rows else "net_mode"
        if cfg.leverage > 0:
            body = {"instId": cfg.okx_inst_id, "lever": str(cfg.leverage), "mgnMode": cfg.okx_margin_mode}
            self.client.post_private("/api/v5/account/set-leverage", body)

    @staticmethod
    def _text(v: Decimal) -> str: return format(v, "f")
    def _step(self, v: Decimal, step: Decimal, rounding) -> Decimal: return (v / step).to_integral_value(rounding=rounding) * step
    def _round_price(self, px: float) -> Decimal: return self._step(Decimal(str(px)), self.tick_size, ROUND_HALF_UP)

    def _validate_market(self) -> dict:
        rows = self.client.get_public("/api/v5/public/instruments", {"instType": "SWAP", "instId": self.cfg.okx_inst_id})
        if not rows: raise RuntimeError(f"OKX instrument not found: {self.cfg.okx_inst_id}")
        item = rows[0]
        if item.get("ctType") != "linear": raise RuntimeError("Only linear OKX swaps are supported")
        return item

    def mid(self) -> float:
        t = self.client.get_public("/api/v5/market/ticker", {"instId": self.cfg.okx_inst_id})[0]
        bid, ask = Decimal(str(t.get("bidPx") or 0)), Decimal(str(t.get("askPx") or 0))
        return float((bid + ask) / 2 if bid > 0 and ask > 0 else Decimal(str(t["last"])))

    def position(self) -> Position:
        rows = self.client.get_private("/api/v5/account/positions", {"instId": self.cfg.okx_inst_id})
        active = []
        for row in rows:
            pos = Decimal(str(row.get("pos") or 0))
            if pos == 0: continue
            side = row.get("posSide", "net")
            signed = -abs(pos) if side == "short" else abs(pos) if side == "long" else pos
            entry = row.get("avgPx") or row.get("openAvgPx")
            active.append(Position(float(signed), float(entry) if entry else None))
        if len(active) > 1: raise RuntimeError("Both LONG and SHORT OKX positions are open")
        return active[0] if active else Position(0.0, None)

    def _ioc(self, side: str, contracts: Decimal, reduce_only: bool = False, pos_side: Optional[str] = None) -> dict:
        ref = Decimal(str(self.mid()))
        slip = Decimal(str(self.cfg.max_slippage))
        is_buy = side == "buy"
        raw_px = ref * (Decimal("1") + slip if is_buy else Decimal("1") - slip)
        px = self._step(raw_px, self.tick_size, ROUND_UP if is_buy else ROUND_DOWN)
        body = {"instId": self.cfg.okx_inst_id, "tdMode": self.cfg.okx_margin_mode, "side": side, "ordType": "ioc", "sz": self._text(contracts), "px": self._text(px), "clOrdId": f"herman{uuid.uuid4().hex[:20]}"[:32]}
        if self.position_mode == "long_short_mode": body["posSide"] = pos_side or ("long" if side == "buy" else "short")
        else: body["reduceOnly"] = reduce_only
        rows = self.client.post_private("/api/v5/trade/order", body)
        if not rows: raise RuntimeError("OKX order returned no result")
        return rows[0]

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        ref = Decimal(str(self.mid()))
        contracts = self._step(Decimal(str(notional_usdc)) / (ref * self.ct_val), self.lot_size, ROUND_DOWN)
        if contracts < self.min_size: raise RuntimeError("OKX size below minSz")
        return self._ioc("buy" if is_buy else "sell", contracts)

    def close_market(self) -> dict:
        pos = self.position()
        if pos.flat: return {"status": "ok", "already_flat": True}
        contracts = self._step(Decimal(str(abs(pos.size))), self.lot_size, ROUND_DOWN)
        side = "sell" if pos.size > 0 else "buy"
        pos_side = "long" if pos.size > 0 else "short"
        return self._ioc(side, contracts, reduce_only=True, pos_side=pos_side)

    def _protection_base(self, position_size: float) -> dict:
        size = self._step(Decimal(str(abs(position_size))), self.lot_size, ROUND_DOWN)
        body = {"instId": self.cfg.okx_inst_id, "tdMode": self.cfg.okx_margin_mode, "side": "sell" if position_size > 0 else "buy", "sz": self._text(size)}
        if self.position_mode == "long_short_mode": body["posSide"] = "long" if position_size > 0 else "short"
        else: body["reduceOnly"] = True
        return body

    def place_protection(self, position_size: float, tp: float, sl: float):
        body = self._protection_base(position_size)
        body.update(ordType="oco", algoClOrdId=f"herman{uuid.uuid4().hex[:20]}"[:32], tpTriggerPx=self._text(self._round_price(tp)), tpOrdPx="-1", tpTriggerPxType="last", slTriggerPx=self._text(self._round_price(sl)), slOrdPx="-1", slTriggerPxType="last")
        rows = self.client.post_private("/api/v5/trade/order-algo", body)
        if not rows or not rows[0].get("algoId"): raise RuntimeError(f"OKX protection failed: {rows}")
        oid = int(rows[0]["algoId"]); return oid, oid

    def update_tp(self, position_size: float, old_tp_oid: Optional[int], tp: float):
        if old_tp_oid is None: return self.place_protection(position_size, tp, tp)[0]
        body = {"instId": self.cfg.okx_inst_id, "algoId": str(old_tp_oid), "newTpTriggerPx": self._text(self._round_price(tp)), "newTpOrdPx": "-1", "newTpTriggerPxType": "last", "cxlOnFail": False}
        try: self.client.post_private("/api/v5/trade/amend-algos", body)
        except OKXAPIError as exc: log.warning("OKX TP amendment failed: %s", exc)
        return int(old_tp_oid)

    def cancel_oid(self, oid: Optional[int]) -> None:
        if oid is None: return
        try: self.client.post_private("/api/v5/trade/cancel-algos", [{"instId": self.cfg.okx_inst_id, "algoId": str(oid)}])
        except Exception as exc: log.warning("OKX cancel failed: %s", exc)

    def _pending(self) -> list[dict]:
        rows = self.client.get_private("/api/v5/trade/orders-algo-pending", {"ordType": "conditional", "instId": self.cfg.okx_inst_id})
        rows += self.client.get_private("/api/v5/trade/orders-algo-pending", {"ordType": "oco", "instId": self.cfg.okx_inst_id})
        return rows

    def cancel_all_protection(self) -> None:
        ids = []
        for row in self._pending():
            if row.get("algoId"): ids.append({"instId": self.cfg.okx_inst_id, "algoId": str(row["algoId"])})
        for i in range(0, len(ids), 10):
            if ids[i:i+10]: self.client.post_private("/api/v5/trade/cancel-algos", ids[i:i+10])

    def recover_protection(self):
        tp_oid = tp_px = sl_oid = sl_px = None
        rows = sorted(self._pending(), key=lambda r: int(r.get("cTime") or 0), reverse=True)
        for row in rows:
            aid = row.get("algoId")
            if not aid: continue
            if tp_px is None and row.get("tpTriggerPx") not in (None, ""): tp_oid, tp_px = int(aid), float(row["tpTriggerPx"])
            if sl_px is None and row.get("slTriggerPx") not in (None, ""): sl_oid, sl_px = int(aid), float(row["slTriggerPx"])
            if tp_px is not None and sl_px is not None: break
        return tp_oid, tp_px, sl_oid, sl_px


def build_executor(cfg: Config) -> BaseExecutor:
    if cfg.dry_run:
        return DryRunExecutor(cfg)
    if cfg.exchange == "okx":
        return OKXExecutor(cfg)
    if cfg.exchange == "lighter":
        return LighterExecutor(cfg)
    return HyperliquidExecutor(cfg)
