from __future__ import annotations

from decimal import Decimal

from .executor import DryRunExecutor, HyperliquidExecutor, OKXExecutor, Position


class MultiDryRunExecutor(DryRunExecutor):
    def close_market(self, position_size: float) -> dict:
        self._position = Position(0.0, None)
        self.cancel_all_protection()
        return {"status": "ok", "dry_run": True, "closed": True}


class MultiHyperliquidExecutor(HyperliquidExecutor):
    def close_market(self, position_size: float) -> dict:
        if abs(position_size) < 1e-15:
            return {"status": "ok", "already_flat": True}
        return self.exchange.market_close(
            self.cfg.coin,
            sz=self._round_size(abs(position_size)),
            slippage=self.cfg.max_slippage,
        )


class MultiOKXExecutor(OKXExecutor):
    def close_market(self, position_size: float) -> dict:
        size = self._round_size(position_size)
        if size < self.min_size:
            return {"status": "ok", "already_flat": True}
        is_buy = position_size < 0
        reference = Decimal(str(self.mid()))
        body = {
            "instId": self.cfg.okx_inst_id,
            "tdMode": self.cfg.okx_margin_mode,
            "side": "buy" if is_buy else "sell",
            "ordType": "ioc",
            "sz": self._text(size),
            "px": self._text(self._entry_limit_price(is_buy, reference)),
            "clOrdId": self._client_id("close"),
        }
        if self.position_mode == "long_short_mode":
            body["posSide"] = "short" if position_size < 0 else "long"
        else:
            body["reduceOnly"] = True
        return self._post_order(body)
