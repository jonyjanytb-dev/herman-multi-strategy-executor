from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP, ROUND_UP
from typing import Callable, Optional

from .config import Config
from .okx_client import OKXAPIError, OKXClient

log = logging.getLogger(__name__)
PERP_MAX_DECIMALS = 6
PRICE_SIGNIFICANT_FIGURES = 5


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
    def _next_oid(self): self._oid += 1; return self._oid
    def place_protection(self, position_size, tp, sl):
        self._sl_oid, self._tp_oid = self._next_oid(), self._next_oid()
        log.info("DRY RUN protection SL=%.4f TP=%.4f", sl, tp)
        return self._tp_oid, self._sl_oid
    def update_tp(self, position_size, old_tp_oid, tp):
        self._tp_oid = old_tp_oid or self._next_oid()
        return self._tp_oid
    def cancel_oid(self, oid): return None
    def cancel_all_protection(self): self._tp_oid = self._sl_oid = None


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
            margin_mode = str(self.market_meta.get("marginMode", ""))
            only_isolated = bool(self.market_meta.get("onlyIsolated", False)) or margin_mode in {"strictIsolated", "noCross"}
            self.exchange.update_leverage(cfg.leverage, cfg.coin, is_cross=not only_isolated)

    def _validate_market(self):
        for item in self.info.meta(dex=self.cfg.dex).get("universe", []):
            if item.get("name") == self.cfg.coin: return item
        raise RuntimeError(f"{self.cfg.coin} not found in HIP-3 dex {self.cfg.dex}")

    def _round_size(self, size: float) -> float:
        q = Decimal("1").scaleb(-self.sz_decimals)
        return float(Decimal(str(size)).quantize(q, rounding=ROUND_DOWN))

    def _round_price(self, price: float) -> float:
        significant = float(f"{price:.{PRICE_SIGNIFICANT_FIGURES}g}")
        return round(significant, PERP_MAX_DECIMALS - self.sz_decimals)

    def mid(self) -> float: return float(self.info.all_mids(self.cfg.dex)[self.cfg.coin])

    def position(self) -> Position:
        state = self.info.user_state(self.cfg.account_address, self.cfg.dex)
        for wrapper in state.get("assetPositions", []):
            p = wrapper.get("position", {})
            if p.get("coin") == self.cfg.coin:
                szi = float(p.get("szi", 0.0)); entry = p.get("entryPx")
                return Position(szi, float(entry) if entry not in (None, "") else None)
        return Position(0.0, None)

    def open_market(self, is_buy: bool, notional_usdc: float) -> dict:
        mid = self.mid(); size = self._round_size(notional_usdc / mid)
        if size <= 0: raise RuntimeError("Calculated order size rounded to zero")
        return self.exchange.market_open(self.cfg.coin, is_buy, size, slippage=self.cfg.max_slippage)

    @staticmethod
    def _extract_oid(resp):
        try:
            status = resp["response"]["data"]["statuses"][0]
            if "resting" in status: return int(status["resting"]["oid"])
        except Exception: pass
        return None

    @staticmethod
    def _extract_error(resp):
        try:
            status = resp["response"]["data"]["statuses"][0]
            if "error" in status: return str(status["error"])
        except Exception: pass
        return None

    def _trigger_payload(self, position_size, trigger_px, kind):
        is_buy = position_size < 0; size = self._round_size(abs(position_size))
        if size <= 0: raise RuntimeError("Position size rounded to zero while creating protection")
        trigger_px = self._round_price(trigger_px)
        return is_buy, size, trigger_px, {"trigger": {"triggerPx": trigger_px, "isMarket": True, "tpsl": kind}}

    def _trigger_order(self, position_size, trigger_px, kind):
        is_buy, size, trigger_px, order_type = self._trigger_payload(position_size, trigger_px, kind)
        resp = self.exchange.order(self.cfg.coin, is_buy, size, trigger_px, order_type, reduce_only=True)
        error = self._extract_error(resp)
        if error: raise RuntimeError(f"Failed to create {kind.upper()} trigger order: {error}")
        oid = self._extract_oid(resp)
        if oid is None: raise RuntimeError(f"Failed to create {kind.upper()} trigger order: {resp}")
        log.info("%s trigger placed oid=%s px=%s size=%s", kind.upper(), oid, trigger_px, size)
        return oid

    def place_protection(self, position_size, tp, sl):
        sl_oid = self._trigger_order(position_size, sl, "sl")
        try: tp_oid = self._trigger_order(position_size, tp, "tp")
        except Exception:
            log.exception("TP placement failed after SL was placed; keeping SL oid=%s", sl_oid)
            raise
        return tp_oid, sl_oid

    def update_tp(self, position_size, old_tp_oid, tp):
        if old_tp_oid is None: return self._trigger_order(position_size, tp, "tp")
        is_buy, size, tp, order_type = self._trigger_payload(position_size, tp, "tp")
        try:
            resp = self.exchange.modify_order(int(old_tp_oid), self.cfg.coin, is_buy, size, tp, order_type, reduce_only=True)
            error = self._extract_error(resp)
            if error: raise RuntimeError(error)
            return self._extract_oid(resp) or int(old_tp_oid)
        except Exception as exc:
            log.warning("Dynamic TP modify failed oid=%s (%s); replacing", old_tp_oid, exc)
        new_oid = self._trigger_order(position_size, tp, "tp")
        if new_oid != old_tp_oid: self.cancel_oid(old_tp_oid)
        return new_oid

    def cancel_oid(self, oid):
        if oid is None: return
        try: self.exchange.cancel(self.cfg.coin, int(oid))
        except Exception as exc: log.warning("cancel oid=%s failed: %s", oid, exc)

    def cancel_all_protection(self):
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


class OKXExecutor(BaseExecutor):
    def __init__(self, cfg: Config, client: Optional[OKXClient] = None, sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.client = client or OKXClient(base_url=cfg.okx_base_url, api_key=cfg.okx_api_key, secret_key=cfg.okx_secret_key, passphrase=cfg.okx_passphrase, demo=cfg.okx_demo, timeout=cfg.request_timeout, retry_attempts=cfg.request_retry_attempts)
        self._sleep = sleep
        self.market_meta = self._validate_market()
        self.ct_val = Decimal(str(self.market_meta["ctVal"])); self.lot_size = Decimal(str(self.market_meta["lotSz"])); self.min_size = Decimal(str(self.market_meta["minSz"])); self.tick_size = Decimal(str(self.market_meta["tickSz"]))
        account = self.client.get_private("/api/v5/account/config")
        if not account: raise RuntimeError("OKX account configuration was empty")
        self.position_mode = str(account[0].get("posMode", "net_mode"))
        if self.position_mode not in {"net_mode", "long_short_mode"}: raise RuntimeError(f"Unsupported OKX position mode: {self.position_mode}")
        if cfg.leverage > 0: self._set_leverage(cfg.leverage)

    @staticmethod
    def _text(value: Decimal) -> str: return format(value, "f")
    @staticmethod
    def _result(data: list, operation: str) -> dict:
        if not data: raise RuntimeError(f"OKX {operation} returned no result")
        result = data[0]
        if str(result.get("sCode", "0")) not in {"", "0"}: raise RuntimeError(f"OKX {operation} failed ({result.get('sCode')}): {result.get('sMsg','')}")
        return result

    def _validate_market(self):
        data = self.client.get_public("/api/v5/public/instruments", {"instType":"SWAP","instId":self.cfg.okx_inst_id})
        if not data: raise RuntimeError(f"OKX instrument not found: {self.cfg.okx_inst_id}")
        item = data[0]
        if item.get("state") != "live" or item.get("ctType") != "linear" or item.get("settleCcy") not in {"USDT","USDC"}: raise RuntimeError("Unsupported OKX instrument")
        return item

    def _set_leverage(self, leverage):
        body={"instId":self.cfg.okx_inst_id,"lever":str(leverage),"mgnMode":self.cfg.okx_margin_mode}
        if self.position_mode == "long_short_mode" and self.cfg.okx_margin_mode == "isolated":
            for side in ("long","short"):
                if (side=="long" and self.cfg.enable_longs) or (side=="short" and self.cfg.enable_shorts):
                    self._result(self.client.post_private("/api/v5/account/set-leverage", dict(body,posSide=side)), f"set {side} leverage")
        else: self._result(self.client.post_private("/api/v5/account/set-leverage", body), "set leverage")

    @staticmethod
    def _client_id(kind): return f"herman{kind}{uuid.uuid4().hex[:20]}"[:32]
    def _round_step(self, value, step, rounding): return (value/step).to_integral_value(rounding=rounding)*step
    def _round_size(self, size): return self._round_step(Decimal(str(abs(size))), self.lot_size, ROUND_DOWN)
    def _round_price(self, price): return self._round_step(Decimal(str(price)), self.tick_size, ROUND_HALF_UP)
    def _entry_limit_price(self, is_buy, reference):
        slip=Decimal(str(self.cfg.max_slippage)); raw=reference*(Decimal("1")+slip if is_buy else Decimal("1")-slip)
        return self._round_step(raw,self.tick_size,ROUND_UP if is_buy else ROUND_DOWN)

    def _recover_by_client_id(self,path,key,client_id,include_inst_id):
        params={key:client_id}
        if include_inst_id: params["instId"]=self.cfg.okx_inst_id
        for attempt in range(3):
            try:
                rows=self.client.get_private(path,params)
                if rows:return rows[0]
            except OKXAPIError: pass
            if attempt<2:self._sleep(0.2)
        return None

    def _post_order(self,body):
        try:return self._result(self.client.post_private("/api/v5/trade/order",body),"order")
        except OKXAPIError as original:
            recovered=self._recover_by_client_id("/api/v5/trade/order","clOrdId",body["clOrdId"],True)
            if recovered is None:raise original
            return recovered
    def _post_algo(self,body,operation):
        try:return self._result(self.client.post_private("/api/v5/trade/order-algo",body),operation)
        except OKXAPIError as original:
            recovered=self._recover_by_client_id("/api/v5/trade/order-algo","algoClOrdId",body["algoClOrdId"],False)
            if recovered is None:raise original
            return recovered

    def mid(self):
        data=self.client.get_public("/api/v5/market/ticker",{"instId":self.cfg.okx_inst_id})
        if not data:raise RuntimeError("No OKX ticker")
        bid=Decimal(str(data[0].get("bidPx") or "0")); ask=Decimal(str(data[0].get("askPx") or "0"))
        if bid>0 and ask>0:return float((bid+ask)/2)
        return float(data[0]["last"])

    def position(self):
        rows=self.client.get_private("/api/v5/account/positions",{"instId":self.cfg.okx_inst_id}); active=[]
        for row in rows:
            amount=Decimal(str(row.get("pos") or "0"))
            if row.get("instId")!=self.cfg.okx_inst_id or amount==0:continue
            side=str(row.get("posSide","net")); signed=-abs(amount) if side=="short" else abs(amount) if side=="long" else amount
            entry=row.get("avgPx") or row.get("openAvgPx"); active.append(Position(float(signed),float(entry) if entry not in (None,"") else None))
        if len(active)>1:raise RuntimeError("Both LONG and SHORT positions exist; one-side manager refuses")
        return active[0] if active else Position(0.0,None)

    def open_market(self,is_buy,notional_usdc):
        ref=Decimal(str(self.mid())); contracts=self._round_step(Decimal(str(notional_usdc))/(ref*self.ct_val),self.lot_size,ROUND_DOWN)
        if contracts<self.min_size:raise RuntimeError("Calculated OKX order size is below minSz")
        body={"instId":self.cfg.okx_inst_id,"tdMode":self.cfg.okx_margin_mode,"side":"buy" if is_buy else "sell","ordType":"ioc","sz":self._text(contracts),"px":self._text(self._entry_limit_price(is_buy,ref)),"clOrdId":self._client_id("entry")}
        if self.position_mode=="long_short_mode":body["posSide"]="long" if is_buy else "short"
        else:body["reduceOnly"]=False
        result=self._post_order(body); oid=result.get("ordId")
        if not oid:raise RuntimeError(f"OKX entry returned no ordId: {result}")
        deadline=time.monotonic()+self.cfg.okx_fill_timeout
        while time.monotonic()<deadline:
            details=self.client.get_private("/api/v5/trade/order",{"instId":self.cfg.okx_inst_id,"ordId":oid})
            if details and details[0].get("state") in {"filled","canceled"}:break
            self._sleep(0.2)
        return result

    def _protection_base(self,position_size):
        size=self._round_size(position_size)
        if size<self.min_size:raise RuntimeError("OKX position size below minSz")
        body={"instId":self.cfg.okx_inst_id,"tdMode":self.cfg.okx_margin_mode,"side":"sell" if position_size>0 else "buy","sz":self._text(size)}
        if self.position_mode=="long_short_mode":body["posSide"]="long" if position_size>0 else "short"
        else:body["reduceOnly"]=True
        return body

    def _place_single_protection(self,position_size,price,kind):
        body=self._protection_base(position_size); rounded=self._text(self._round_price(price)); body.update({"ordType":"conditional","algoClOrdId":self._client_id(kind)})
        if kind=="tp":body.update(tpTriggerPx=rounded,tpOrdPx="-1",tpTriggerPxType=self.cfg.okx_trigger_price_type)
        else:body.update(slTriggerPx=rounded,slOrdPx="-1",slTriggerPxType=self.cfg.okx_trigger_price_type)
        result=self._post_algo(body,f"{kind.upper()} protection"); oid=result.get("algoId")
        if not oid:raise RuntimeError(f"OKX {kind} returned no algoId")
        return int(oid)

    def place_protection(self,position_size,tp,sl):
        body=self._protection_base(position_size); body.update(ordType="oco",algoClOrdId=self._client_id("protect"),tpTriggerPx=self._text(self._round_price(tp)),tpOrdPx="-1",tpTriggerPxType=self.cfg.okx_trigger_price_type,slTriggerPx=self._text(self._round_price(sl)),slOrdPx="-1",slTriggerPxType=self.cfg.okx_trigger_price_type)
        result=self._post_algo(body,"OCO protection"); oid=result.get("algoId")
        if not oid:raise RuntimeError("OKX protection returned no algoId")
        return int(oid),int(oid)

    def update_tp(self,position_size,old_tp_oid,tp):
        if old_tp_oid is None:return self._place_single_protection(position_size,tp,"tp")
        body={"instId":self.cfg.okx_inst_id,"algoId":str(old_tp_oid),"newTpTriggerPx":self._text(self._round_price(tp)),"newTpOrdPx":"-1","newTpTriggerPxType":self.cfg.okx_trigger_price_type,"cxlOnFail":False}
        try:self._result(self.client.post_private("/api/v5/trade/amend-algos",body),"dynamic TP amendment")
        except (OKXAPIError,RuntimeError) as exc:log.warning("OKX Dynamic TP amendment failed algoId=%s: %s",old_tp_oid,exc)
        return int(old_tp_oid)

    def cancel_oid(self,oid):
        if oid is None:return
        try:self._result(self.client.post_private("/api/v5/trade/cancel-algos",[{"instId":self.cfg.okx_inst_id,"algoId":str(oid)}]),"cancel protection")
        except Exception as exc:log.warning("OKX cancel algoId=%s failed: %s",oid,exc)

    def _pending_protection(self):
        rows=self.client.get_private("/api/v5/trade/orders-algo-pending",{"ordType":"conditional","instId":self.cfg.okx_inst_id})
        rows+=self.client.get_private("/api/v5/trade/orders-algo-pending",{"ordType":"oco","instId":self.cfg.okx_inst_id})
        return [x for x in rows if x.get("instId")==self.cfg.okx_inst_id]

    def cancel_all_protection(self):
        for row in self._pending_protection():
            if row.get("algoId") and (row.get("tpTriggerPx") or row.get("slTriggerPx")):self.cancel_oid(int(row["algoId"]))

    def recover_protection(self):
        tp_oid=tp_px=sl_oid=sl_px=None
        for row in sorted(self._pending_protection(),key=lambda x:int(x.get("cTime") or 0),reverse=True):
            aid=row.get("algoId")
            if not aid:continue
            if tp_px is None and row.get("tpTriggerPx") not in (None,""):tp_oid,tp_px=int(aid),float(row["tpTriggerPx"])
            if sl_px is None and row.get("slTriggerPx") not in (None,""):sl_oid,sl_px=int(aid),float(row["slTriggerPx"])
            if tp_px is not None and sl_px is not None:break
        return tp_oid,tp_px,sl_oid,sl_px
