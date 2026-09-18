from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv

from .credentials import valid_eth_address, valid_private_key

DEFAULT_OKX_INST_ID = "US100-USDT-SWAP"


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _text(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


@dataclass(frozen=True)
class Config:
    strategy: str
    dry_run: bool
    exchange: str
    network: str
    dex: str
    coin: str
    interval: str
    account_address: str
    api_private_key: str
    order_notional_usdc: float
    leverage: int
    max_slippage: float
    enable_longs: bool
    enable_shorts: bool
    poll_seconds: float
    lookback_candles: int
    state_path: str
    log_level: str

    okx_inst_id: str
    okx_api_key: str
    okx_secret_key: str
    okx_passphrase: str
    okx_demo: bool
    okx_margin_mode: str
    okx_base_url: str

    trend_sma50_length: int
    trend_sma200_length: int
    trend_point_size: float
    trend_min_separation: float
    trend_tp_mode: str
    trend_sma_target_behaviour: str
    trend_tp_fixed_points: float
    trend_sl_mode: str
    trend_sl_fixed_points: float

    streak_signal_timeframe: str
    streak_definition: str
    streak_length: int
    streak_wait_bars: int
    streak_use_session_filter: bool
    streak_session_start: str
    streak_session_end: str
    streak_trigger_must_be_in_session: bool
    streak_hard_flat: bool
    streak_hard_flat_time: str
    streak_sl_mode: str
    streak_stop_buffer_points: float
    streak_atr_length: int
    streak_atr_mult: float
    streak_tp_mode: str
    streak_tp_r: float
    streak_invalid_target_policy: str

    aw_swing_length: int
    aw_max_liquidity: int
    aw_use_htf_liquidity: bool
    aw_htf_minutes: int
    aw_htf_pivot_strength: int
    aw_use_pdhl: bool
    aw_atr_length: int
    aw_displacement_mult: float
    aw_max_bars_to_mss: int
    aw_fvg_search_window: int
    aw_max_bars_to_entry: int
    aw_use_asia: bool
    aw_use_london: bool
    aw_use_nyam: bool
    aw_use_nypm: bool
    aw_target_mode: str
    aw_target_r: float

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        strategy = _text("STRATEGY", "trend_rebalance").lower()
        exchange = _text("EXCHANGE", "hyperliquid").lower()
        explicit_state = _text("STATE_PATH", "")
        state_path = explicit_state or f"runtime/state-{exchange}-{strategy}.json"
        cfg = cls(
            strategy=strategy,
            dry_run=_bool("DRY_RUN", True),
            exchange=exchange,
            network=_text("NETWORK", "mainnet").lower(),
            dex=_text("DEX", "xyz"),
            coin=_text("COIN", "xyz:XYZ100"),
            interval=_text("INTERVAL", "1m"),
            account_address=_text("ACCOUNT_ADDRESS", ""),
            api_private_key=_text("API_PRIVATE_KEY", ""),
            order_notional_usdc=_float("ORDER_NOTIONAL_USDC", 100.0),
            leverage=_int("LEVERAGE", 0),
            max_slippage=_float("MAX_SLIPPAGE", 0.005),
            enable_longs=_bool("ENABLE_LONGS", True),
            enable_shorts=_bool("ENABLE_SHORTS", True),
            poll_seconds=_float("POLL_SECONDS", 3.0),
            lookback_candles=_int("LOOKBACK_CANDLES", 500),
            state_path=state_path,
            log_level=_text("LOG_LEVEL", "INFO").upper(),
            okx_inst_id=_text("OKX_INST_ID", DEFAULT_OKX_INST_ID).upper(),
            okx_api_key=_text("OKX_API_KEY", ""),
            okx_secret_key=_text("OKX_SECRET_KEY", ""),
            okx_passphrase=_text("OKX_PASSPHRASE", ""),
            okx_demo=_bool("OKX_DEMO", True),
            okx_margin_mode=_text("OKX_MARGIN_MODE", "cross").lower(),
            okx_base_url=_text("OKX_BASE_URL", "https://www.okx.com"),
            trend_sma50_length=_int("TREND_SMA50_LENGTH", 50),
            trend_sma200_length=_int("TREND_SMA200_LENGTH", 200),
            trend_point_size=_float("TREND_POINT_SIZE", 1.0),
            trend_min_separation=_float("TREND_MIN_SEPARATION", 30.0),
            trend_tp_mode=_text("TREND_TP_MODE", "200 SMA"),
            trend_sma_target_behaviour=_text("TREND_SMA_TARGET_BEHAVIOUR", "Dynamic"),
            trend_tp_fixed_points=_float("TREND_TP_FIXED_POINTS", 100.0),
            trend_sl_mode=_text("TREND_SL_MODE", "Fixed Points"),
            trend_sl_fixed_points=_float("TREND_SL_FIXED_POINTS", 125.0),
            streak_signal_timeframe=_text("STREAK_SIGNAL_TIMEFRAME", "1m").lower(),
            streak_definition=_text("STREAK_DEFINITION", "bodies").lower(),
            streak_length=_int("STREAK_LENGTH", 5),
            streak_wait_bars=_int("STREAK_WAIT_BARS", 15),
            streak_use_session_filter=_bool("STREAK_USE_SESSION_FILTER", True),
            streak_session_start=_text("STREAK_SESSION_START", "09:45"),
            streak_session_end=_text("STREAK_SESSION_END", "12:00"),
            streak_trigger_must_be_in_session=_bool("STREAK_TRIGGER_MUST_BE_IN_SESSION", True),
            streak_hard_flat=_bool("STREAK_HARD_FLAT", True),
            streak_hard_flat_time=_text("STREAK_HARD_FLAT_TIME", "16:00"),
            streak_sl_mode=_text("STREAK_SL_MODE", "terminal").lower(),
            streak_stop_buffer_points=_float("STREAK_STOP_BUFFER_POINTS", 0.0),
            streak_atr_length=_int("STREAK_ATR_LENGTH", 14),
            streak_atr_mult=_float("STREAK_ATR_MULT", 1.0),
            streak_tp_mode=_text("STREAK_TP_MODE", "r_multiple").lower(),
            streak_tp_r=_float("STREAK_TP_R", 1.0),
            streak_invalid_target_policy=_text("STREAK_INVALID_TARGET_POLICY", "use_r").lower(),
            aw_swing_length=_int("AW_SWING_LENGTH", 3),
            aw_max_liquidity=_int("AW_MAX_LIQUIDITY", 15),
            aw_use_htf_liquidity=_bool("AW_USE_HTF_LIQUIDITY", True),
            aw_htf_minutes=_int("AW_HTF_MINUTES", 15),
            aw_htf_pivot_strength=_int("AW_HTF_PIVOT_STRENGTH", 2),
            aw_use_pdhl=_bool("AW_USE_PDHL", True),
            aw_atr_length=_int("AW_ATR_LENGTH", 2),
            aw_displacement_mult=_float("AW_DISPLACEMENT_MULT", 1.0),
            aw_max_bars_to_mss=_int("AW_MAX_BARS_TO_MSS", 30),
            aw_fvg_search_window=_int("AW_FVG_SEARCH_WINDOW", 1),
            aw_max_bars_to_entry=_int("AW_MAX_BARS_TO_ENTRY", 10),
            aw_use_asia=_bool("AW_USE_ASIA", True),
            aw_use_london=_bool("AW_USE_LONDON", True),
            aw_use_nyam=_bool("AW_USE_NYAM", True),
            aw_use_nypm=_bool("AW_USE_NYPM", False),
            aw_target_mode=_text("AW_TARGET_MODE", "opposite_liquidity").lower(),
            aw_target_r=_float("AW_TARGET_R", 1.0),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.strategy not in {"trend_rebalance", "streak_failure", "aw_liquidity"}:
            raise ValueError("STRATEGY must be trend_rebalance, streak_failure, or aw_liquidity")
        if self.exchange not in {"hyperliquid", "okx"}:
            raise ValueError("EXCHANGE must be hyperliquid or okx")
        if self.interval != "1m":
            raise ValueError("INTERVAL must remain 1m")
        if self.order_notional_usdc <= 0:
            raise ValueError("ORDER_NOTIONAL_USDC must be > 0")
        if self.max_slippage <= 0 or self.max_slippage > 0.05:
            raise ValueError("MAX_SLIPPAGE must be > 0 and <= 0.05")
        if self.trend_point_size <= 0:
            raise ValueError("TREND_POINT_SIZE must be > 0")
        if self.trend_tp_mode not in {"200 SMA", "Fixed Points"}:
            raise ValueError("TREND_TP_MODE invalid")
        if self.trend_sma_target_behaviour not in {"Locked at Entry", "Dynamic"}:
            raise ValueError("TREND_SMA_TARGET_BEHAVIOUR invalid")
        if self.trend_sl_mode not in {"1R to TP", "Fixed Points"}:
            raise ValueError("TREND_SL_MODE invalid")
        if self.streak_signal_timeframe not in {"1m", "5m"}:
            raise ValueError("STREAK_SIGNAL_TIMEFRAME must be 1m or 5m")
        if self.streak_definition not in {"bodies", "higher_lower_closes"}:
            raise ValueError("STREAK_DEFINITION must be bodies or higher_lower_closes")
        if self.streak_length < 2:
            raise ValueError("STREAK_LENGTH must be >= 2")
        if self.streak_wait_bars < 1:
            raise ValueError("STREAK_WAIT_BARS must be >= 1")
        if self.streak_sl_mode not in {"trigger", "terminal", "whole_streak", "whole_move", "atr"}:
            raise ValueError("STREAK_SL_MODE invalid")
        if self.streak_tp_mode not in {"r_multiple", "first_extreme", "first_open"}:
            raise ValueError("STREAK_TP_MODE invalid")
        if self.streak_invalid_target_policy not in {"use_r", "skip"}:
            raise ValueError("STREAK_INVALID_TARGET_POLICY invalid")
        if self.aw_swing_length < 2:
            raise ValueError("AW_SWING_LENGTH must be >= 2")
        if not 5 <= self.aw_max_liquidity <= 40:
            raise ValueError("AW_MAX_LIQUIDITY must be between 5 and 40")
        if self.aw_htf_minutes <= 1:
            raise ValueError("AW_HTF_MINUTES must be > 1")
        if self.aw_htf_pivot_strength < 1:
            raise ValueError("AW_HTF_PIVOT_STRENGTH must be >= 1")
        if self.aw_atr_length < 1:
            raise ValueError("AW_ATR_LENGTH must be >= 1")
        if self.aw_displacement_mult <= 0:
            raise ValueError("AW_DISPLACEMENT_MULT must be > 0")
        if self.aw_max_bars_to_mss < 3:
            raise ValueError("AW_MAX_BARS_TO_MSS must be >= 3")
        if not 0 <= self.aw_fvg_search_window <= 5:
            raise ValueError("AW_FVG_SEARCH_WINDOW must be between 0 and 5")
        if self.aw_max_bars_to_entry < 3:
            raise ValueError("AW_MAX_BARS_TO_ENTRY must be >= 3")
        if self.aw_target_mode not in {"opposite_liquidity", "fixed_r"}:
            raise ValueError("AW_TARGET_MODE must be opposite_liquidity or fixed_r")
        if self.aw_target_r < 0.5:
            raise ValueError("AW_TARGET_R must be >= 0.5")
        if self.exchange == "hyperliquid":
            if self.network not in {"mainnet", "testnet"}:
                raise ValueError("NETWORK must be mainnet or testnet")
            if ":" not in self.coin or not self.coin.startswith(self.dex + ":"):
                raise ValueError("COIN must use HIP-3 prefixed form, e.g. xyz:XYZ100")
            if not self.dry_run:
                if not self.account_address or not self.api_private_key:
                    raise ValueError("ACCOUNT_ADDRESS and API_PRIVATE_KEY required when LIVE")
                if not valid_eth_address(self.account_address):
                    raise ValueError("ACCOUNT_ADDRESS 格式错误：必须是 0x 开头的 40 位十六进制地址")
                if not valid_private_key(self.api_private_key):
                    raise ValueError("API_PRIVATE_KEY 格式错误：必须是 32-byte 十六进制私钥，可带 0x 前缀")
        else:
            if not self.okx_inst_id.endswith("-SWAP"):
                raise ValueError("OKX_INST_ID must end in -SWAP")
            if self.okx_margin_mode not in {"cross", "isolated"}:
                raise ValueError("OKX_MARGIN_MODE must be cross or isolated")
            parsed = urlparse(self.okx_base_url)
            if parsed.scheme != "https" or parsed.hostname not in {"www.okx.com", "my.okx.com", "app.okx.com"}:
                raise ValueError("OKX_BASE_URL must be an official OKX HTTPS origin")
            if not self.dry_run and not all((self.okx_api_key, self.okx_secret_key, self.okx_passphrase)):
                raise ValueError("OKX API credentials required when DRY_RUN=false")

    @property
    def market_symbol(self) -> str:
        return self.okx_inst_id if self.exchange == "okx" else self.coin

    @property
    def execution_mode(self) -> str:
        if self.dry_run:
            return "dry_run"
        if self.exchange == "okx" and self.okx_demo:
            return "demo"
        return "live"

    @property
    def strategy_label(self) -> str:
        labels = {
            "trend_rebalance": "1.0 Trend Rebalance Map",
            "streak_failure": "1.1 Streak Failure Reversal",
            "aw_liquidity": "1.2 AW Liquidity Reversal",
        }
        return labels[self.strategy]
