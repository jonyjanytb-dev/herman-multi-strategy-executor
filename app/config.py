from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlparse

from dotenv import load_dotenv


DEFAULT_OKX_INST_ID = "US100-USDT-SWAP"
STRATEGIES = {"trend_rebalance", "streak_failure"}
STREAK_SL_MODES = {
    "Trigger candle extreme",
    "Terminal streak candle extreme",
    "Whole streak extreme",
    "Whole move through trigger",
    "ATR from trigger close",
}
STREAK_TP_MODES = {"R multiple", "First streak candle extreme", "First streak candle open"}


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


def _parse_hhmm(value: str, name: str) -> tuple[int, int]:
    try:
        hh, mm = value.split(":", 1)
        hour, minute = int(hh), int(mm)
    except Exception as exc:
        raise ValueError(f"{name} must be HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(f"{name} must be a valid HH:MM time")
    return hour, minute


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

    sma50_length: int
    sma200_length: int
    point_size: float
    min_separation: float
    tp_mode: str
    sma_target_behaviour: str
    tp_fixed_points: float
    sl_mode: str
    sl_fixed_points: float

    streak_signal_timeframe: str
    streak_definition: str
    streak_length: int
    streak_wait_bars: int
    streak_use_session_filter: bool
    streak_session_start: str
    streak_session_end: str
    streak_trigger_must_be_in_session: bool
    streak_hard_flat_enabled: bool
    streak_hard_flat: str
    streak_sl_mode: str
    streak_stop_buffer_ticks: int
    streak_tick_size: float
    streak_atr_length: int
    streak_atr_mult: float
    streak_tp_mode: str
    streak_tp_r: float
    streak_invalid_target_policy: str
    streak_sizing_mode: str
    streak_max_risk_usd: float
    streak_max_notional_usd: float
    streak_sizing_slippage_ticks: int

    poll_seconds: float
    lookback_candles: int
    state_path: str
    log_level: str
    entry_position_wait_seconds: float

    okx_inst_id: str = DEFAULT_OKX_INST_ID
    okx_api_key: str = ""
    okx_secret_key: str = ""
    okx_passphrase: str = ""
    okx_demo: bool = True
    okx_margin_mode: str = "cross"
    okx_trigger_price_type: str = "last"
    okx_base_url: str = "https://www.okx.com"
    request_timeout: float = 15.0
    request_retry_attempts: int = 3
    okx_fill_timeout: float = 10.0

    @classmethod
    def load(cls) -> "Config":
        load_dotenv()
        strategy = _text("STRATEGY", "trend_rebalance").lower()
        exchange = _text("EXCHANGE", "hyperliquid").lower()
        default_state = f"runtime/state-{exchange}-{strategy}.json"
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
            sma50_length=_int("SMA50_LENGTH", 50),
            sma200_length=_int("SMA200_LENGTH", 200),
            point_size=_float("POINT_SIZE", 1.0),
            min_separation=_float("MIN_SEPARATION", 30.0),
            tp_mode=_text("TP_MODE", "200 SMA"),
            sma_target_behaviour=_text("SMA_TARGET_BEHAVIOUR", "Dynamic"),
            tp_fixed_points=_float("TP_FIXED_POINTS", 100.0),
            sl_mode=_text("SL_MODE", "Fixed Points"),
            sl_fixed_points=_float("SL_FIXED_POINTS", 125.0),
            streak_signal_timeframe=_text("STREAK_SIGNAL_TIMEFRAME", "1m").lower(),
            streak_definition=_text("STREAK_DEFINITION", "bodies").lower(),
            streak_length=_int("STREAK_LENGTH", 5),
            streak_wait_bars=_int("STREAK_WAIT_BARS", 15),
            streak_use_session_filter=_bool("STREAK_USE_SESSION_FILTER", True),
            streak_session_start=_text("STREAK_SESSION_START", "09:45"),
            streak_session_end=_text("STREAK_SESSION_END", "12:00"),
            streak_trigger_must_be_in_session=_bool("STREAK_TRIGGER_MUST_BE_IN_SESSION", True),
            streak_hard_flat_enabled=_bool("STREAK_HARD_FLAT_ENABLED", True),
            streak_hard_flat=_text("STREAK_HARD_FLAT", "16:00"),
            streak_sl_mode=_text("STREAK_SL_MODE", "Terminal streak candle extreme"),
            streak_stop_buffer_ticks=_int("STREAK_STOP_BUFFER_TICKS", 0),
            streak_tick_size=_float("STREAK_TICK_SIZE", 1.0),
            streak_atr_length=_int("STREAK_ATR_LENGTH", 14),
            streak_atr_mult=_float("STREAK_ATR_MULT", 1.0),
            streak_tp_mode=_text("STREAK_TP_MODE", "R multiple"),
            streak_tp_r=_float("STREAK_TP_R", 1.0),
            streak_invalid_target_policy=_text("STREAK_INVALID_TARGET_POLICY", "Use R target"),
            streak_sizing_mode=_text("STREAK_SIZING_MODE", "Fixed notional"),
            streak_max_risk_usd=_float("STREAK_MAX_RISK_USD", 200.0),
            streak_max_notional_usd=_float("STREAK_MAX_NOTIONAL_USD", 10_000.0),
            streak_sizing_slippage_ticks=_int("STREAK_SIZING_SLIPPAGE_TICKS", 1),
            poll_seconds=_float("POLL_SECONDS", 3.0),
            lookback_candles=_int("LOOKBACK_CANDLES", 320),
            state_path=_text("STATE_PATH", "") or default_state,
            log_level=_text("LOG_LEVEL", "INFO").upper(),
            entry_position_wait_seconds=_float("ENTRY_POSITION_WAIT_SECONDS", 5.0),
            okx_inst_id=_text("OKX_INST_ID", DEFAULT_OKX_INST_ID).upper() or DEFAULT_OKX_INST_ID,
            okx_api_key=_text("OKX_API_KEY", ""),
            okx_secret_key=_text("OKX_SECRET_KEY", ""),
            okx_passphrase=_text("OKX_PASSPHRASE", ""),
            okx_demo=_bool("OKX_DEMO", True),
            okx_margin_mode=_text("OKX_MARGIN_MODE", "cross").lower(),
            okx_trigger_price_type=_text("OKX_TRIGGER_PRICE_TYPE", "last").lower(),
            okx_base_url=_text("OKX_BASE_URL", "https://www.okx.com"),
            request_timeout=_float("REQUEST_TIMEOUT", 15.0),
            request_retry_attempts=_int("REQUEST_RETRY_ATTEMPTS", 3),
            okx_fill_timeout=_float("OKX_FILL_TIMEOUT", 10.0),
        )
        cfg.validate()
        return cfg

    def validate(self) -> None:
        if self.strategy not in STRATEGIES:
            raise ValueError("STRATEGY must be trend_rebalance or streak_failure")
        if self.exchange not in {"hyperliquid", "okx"}:
            raise ValueError("EXCHANGE must be hyperliquid or okx")
        if self.network not in {"mainnet", "testnet"}:
            raise ValueError("NETWORK must be mainnet or testnet")
        if self.interval != "1m":
            raise ValueError("Chart/execution INTERVAL is intentionally fixed to 1m")
        if self.order_notional_usdc <= 0:
            raise ValueError("ORDER_NOTIONAL_USDC must be > 0")
        if self.leverage < 0:
            raise ValueError("LEVERAGE must be >= 0")
        if self.max_slippage <= 0 or self.max_slippage > 0.05:
            raise ValueError("MAX_SLIPPAGE must be > 0 and <= 0.05")
        if self.poll_seconds <= 0 or self.lookback_candles < 220:
            raise ValueError("POLL_SECONDS must be > 0 and LOOKBACK_CANDLES must be >= 220")
        if self.entry_position_wait_seconds <= 0 or self.entry_position_wait_seconds > 30:
            raise ValueError("ENTRY_POSITION_WAIT_SECONDS must be in (0, 30]")
        if self.sma50_length < 1 or self.sma200_length < 1:
            raise ValueError("SMA lengths must be >= 1")
        if self.point_size <= 0 or self.min_separation < 0:
            raise ValueError("POINT_SIZE must be > 0 and MIN_SEPARATION must be >= 0")
        if self.tp_mode not in {"200 SMA", "Fixed Points"}:
            raise ValueError("TP_MODE must be '200 SMA' or 'Fixed Points'")
        if self.sma_target_behaviour not in {"Locked at Entry", "Dynamic"}:
            raise ValueError("SMA_TARGET_BEHAVIOUR invalid")
        if self.sl_mode not in {"1R to TP", "Fixed Points"}:
            raise ValueError("SL_MODE must be '1R to TP' or 'Fixed Points'")
        if self.streak_signal_timeframe not in {"1m", "5m"}:
            raise ValueError("STREAK_SIGNAL_TIMEFRAME must be 1m or 5m")
        if self.streak_definition not in {"bodies", "higher_lower_closes"}:
            raise ValueError("STREAK_DEFINITION must be bodies or higher_lower_closes")
        if not (2 <= self.streak_length <= 20):
            raise ValueError("STREAK_LENGTH must be between 2 and 20")
        if not (1 <= self.streak_wait_bars <= 100):
            raise ValueError("STREAK_WAIT_BARS must be between 1 and 100")
        _parse_hhmm(self.streak_session_start, "STREAK_SESSION_START")
        _parse_hhmm(self.streak_session_end, "STREAK_SESSION_END")
        _parse_hhmm(self.streak_hard_flat, "STREAK_HARD_FLAT")
        if self.streak_sl_mode not in STREAK_SL_MODES:
            raise ValueError(f"STREAK_SL_MODE invalid: {self.streak_sl_mode}")
        if self.streak_stop_buffer_ticks < 0 or self.streak_tick_size <= 0:
            raise ValueError("STREAK_STOP_BUFFER_TICKS must be >=0 and STREAK_TICK_SIZE >0")
        if self.streak_atr_length < 1 or self.streak_atr_mult <= 0:
            raise ValueError("STREAK_ATR_LENGTH >=1 and STREAK_ATR_MULT >0 required")
        if self.streak_tp_mode not in STREAK_TP_MODES:
            raise ValueError(f"STREAK_TP_MODE invalid: {self.streak_tp_mode}")
        if self.streak_tp_r < 0.25:
            raise ValueError("STREAK_TP_R must be >= 0.25")
        if self.streak_invalid_target_policy not in {"Use R target", "Skip setup"}:
            raise ValueError("STREAK_INVALID_TARGET_POLICY invalid")
        if self.streak_sizing_mode not in {"Fixed notional", "Stop-risk budget"}:
            raise ValueError("STREAK_SIZING_MODE must be Fixed notional or Stop-risk budget")
        if self.streak_max_risk_usd <= 0 or self.streak_max_notional_usd <= 0:
            raise ValueError("Streak risk/notional limits must be > 0")
        if self.streak_sizing_slippage_ticks < 0:
            raise ValueError("STREAK_SIZING_SLIPPAGE_TICKS must be >= 0")
        if self.request_timeout <= 0 or not (1 <= self.request_retry_attempts <= 5):
            raise ValueError("Invalid request timeout/retry settings")
        if self.okx_fill_timeout <= 0:
            raise ValueError("OKX_FILL_TIMEOUT must be > 0")

        if self.exchange == "hyperliquid":
            if ":" not in self.coin or not self.coin.startswith(self.dex + ":"):
                raise ValueError("COIN must use HIP-3 prefixed form, e.g. xyz:XYZ100")
            if not self.dry_run and (not self.account_address or not self.api_private_key):
                raise ValueError("ACCOUNT_ADDRESS and API_PRIVATE_KEY are required when DRY_RUN=false")
            return

        if not self.okx_inst_id.endswith("-SWAP"):
            raise ValueError("OKX_INST_ID must be a perpetual swap ending in -SWAP")
        if self.okx_margin_mode not in {"cross", "isolated"}:
            raise ValueError("OKX_MARGIN_MODE must be cross or isolated")
        if self.okx_trigger_price_type not in {"last", "index", "mark"}:
            raise ValueError("OKX_TRIGGER_PRICE_TYPE must be last, index or mark")
        parsed = urlparse(self.okx_base_url)
        if parsed.scheme != "https" or parsed.hostname not in {"www.okx.com", "my.okx.com", "app.okx.com"} or parsed.port not in {None, 443} or parsed.username is not None or parsed.password is not None or parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
            raise ValueError("OKX_BASE_URL must be an official OKX HTTPS origin")
        if not self.dry_run and not all((self.okx_api_key, self.okx_secret_key, self.okx_passphrase)):
            raise ValueError("OKX API key/secret/passphrase are required when DRY_RUN=false")

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
        return "1.1 Streak Failure Reversal" if self.strategy == "streak_failure" else "1.0 Trend Rebalance Map"
