from __future__ import annotations

import getpass
import os
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def ensure_env() -> None:
    if not ENV_PATH.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        print("已创建本地 .env（默认 DRY_RUN=true）。")


def read_env() -> dict[str, str]:
    ensure_env()
    raw = dotenv_values(ENV_PATH)
    return {k: str(v or "") for k, v in raw.items()}


def set_env(key: str, value: str) -> None:
    ensure_env()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    prefix = key + "="
    out = []
    found = False
    for line in lines:
        if line.startswith(prefix):
            out.append(prefix + value)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(prefix + value)
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def strategy_name(cfg: dict[str, str]) -> str:
    return cfg.get("STRATEGY", "trend_rebalance").strip().lower() or "trend_rebalance"


def strategy_label(cfg: dict[str, str]) -> str:
    return "1.1 Streak Failure Reversal" if strategy_name(cfg) == "streak_failure" else "1.0 Trend Rebalance Map"


def exchange_name(cfg: dict[str, str]) -> str:
    return cfg.get("EXCHANGE", "hyperliquid").strip().lower() or "hyperliquid"


def mode(cfg: dict[str, str]) -> str:
    if cfg.get("DRY_RUN", "true").lower() == "true":
        return "DRY RUN"
    if exchange_name(cfg) == "okx" and cfg.get("OKX_DEMO", "true").lower() == "true":
        return "OKX DEMO"
    return "LIVE"


def market(cfg: dict[str, str]) -> str:
    return cfg.get("OKX_INST_ID", "US100-USDT-SWAP") if exchange_name(cfg) == "okx" else cfg.get("COIN", "xyz:XYZ100")


def mask(value: str) -> str:
    if not value:
        return "未设置"
    return value[:6] + "..." + value[-4:] if len(value) > 12 else value


def show_status() -> None:
    cfg = read_env()
    notional = float(cfg.get("ORDER_NOTIONAL_USDC", "100") or 100)
    lev = int(cfg.get("LEVERAGE", "0") or 0)
    print("\n" + "=" * 68)
    print(" Herman Multi-Strategy Executor · 本地交互终端")
    print("=" * 68)
    print(f" 策略        : {strategy_label(cfg)}")
    print(f" 交易所      : {'OKX' if exchange_name(cfg) == 'okx' else 'Hyperliquid'}")
    print(f" 模式        : {mode(cfg)}")
    print(f" 市场        : {market(cfg)}")
    print(f" 周期        : 1m")
    print(f" 每笔名义仓位: {notional:.2f}")
    print(f" 杠杆        : {lev}x" if lev > 0 else " 杠杆        : 不自动修改")
    print(f" 做多 / 做空 : {cfg.get('ENABLE_LONGS','true')} / {cfg.get('ENABLE_SHORTS','true')}")
    if strategy_name(cfg) == "trend_rebalance":
        print(f" 逻辑        : SMA50/SMA200 回归 | sep>{cfg.get('TREND_MIN_SEPARATION','30')} points")
        print(f" TP / SL     : {cfg.get('TREND_TP_MODE','200 SMA')} / {cfg.get('TREND_SL_MODE','Fixed Points')}")
    else:
        print(f" 逻辑        : {cfg.get('STREAK_LENGTH','5')} 根 streak → failure confirmation")
        print(f" 信号周期    : {cfg.get('STREAK_SIGNAL_TIMEFRAME','1m')} | window={cfg.get('STREAK_WAIT_BARS','15')}")
        print(f" Session     : {cfg.get('STREAK_SESSION_START','09:45')}–{cfg.get('STREAK_SESSION_END','12:00')} New York")
        print(f" TP / SL     : {cfg.get('STREAK_TP_MODE','r_multiple')} / {cfg.get('STREAK_SL_MODE','terminal')}")
    if exchange_name(cfg) == "hyperliquid":
        print(f" 主账户      : {mask(cfg.get('ACCOUNT_ADDRESS',''))}")
        print(f" API Wallet  : {'已设置' if cfg.get('API_PRIVATE_KEY') else '未设置'}")
    else:
        ready = all(cfg.get(k) for k in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"))
        print(f" OKX API     : {'已设置' if ready else '未设置'}")
    print("=" * 68)


def configure_strategy() -> None:
    cfg = read_env()
    print("\n1) 1.0 Trend Rebalance Map   - SMA 拉伸后的均值回归")
    print("2) 1.1 Streak Failure Reversal - 连续 K 线后的失败反转")
    choice = input("选择策略 [1/2, Enter=保持]: ").strip()
    if not choice:
        return
    target = {"1": "trend_rebalance", "2": "streak_failure"}.get(choice)
    if not target:
        raise ValueError("只能选择 1 或 2")
    if target != strategy_name(cfg):
        set_env("STRATEGY", target)
        set_env("DRY_RUN", "true")
        set_env("STATE_PATH", "")
        print("策略已切换。为安全起见已自动切回 DRY RUN，并使用独立策略状态文件。")


def configure_position() -> None:
    cfg = read_env()
    current = float(cfg.get("ORDER_NOTIONAL_USDC", "100") or 100)
    raw = input(f"每笔名义仓位 [{current}]: ").strip()
    if raw:
        value = float(raw)
        if value <= 0: raise ValueError("仓位必须 > 0")
        set_env("ORDER_NOTIONAL_USDC", str(value))
    lev = input(f"杠杆（0=不自动修改） [{cfg.get('LEVERAGE','0')}]: ").strip()
    if lev:
        value = int(lev)
        if value < 0: raise ValueError("杠杆不能 < 0")
        set_env("LEVERAGE", str(value))


def configure_exchange() -> None:
    cfg = read_env()
    print("\n1) Hyperliquid")
    print("2) OKX")
    choice = input("选择交易所 [1/2, Enter=保持]: ").strip()
    ex = {"1": "hyperliquid", "2": "okx"}.get(choice, exchange_name(cfg))
    if choice not in {"", "1", "2"}: raise ValueError("只能选择 1 或 2")
    if ex != exchange_name(cfg):
        set_env("EXCHANGE", ex)
        set_env("DRY_RUN", "true")
        set_env("STATE_PATH", "")
    cfg = read_env()
    if ex == "hyperliquid":
        addr = input(f"主账户地址 [{mask(cfg.get('ACCOUNT_ADDRESS',''))}]: ").strip()
        if addr: set_env("ACCOUNT_ADDRESS", addr)
        print("API Wallet 私钥不会回显；留空保持原值。")
        secret = getpass.getpass("API Wallet Private Key: ").strip()
        if secret: set_env("API_PRIVATE_KEY", secret)
    else:
        inst = input(f"OKX 永续合约 [{cfg.get('OKX_INST_ID','US100-USDT-SWAP')}]: ").strip().upper()
        if inst: set_env("OKX_INST_ID", inst)
        for key, label in (("OKX_API_KEY","API Key"),("OKX_SECRET_KEY","Secret Key"),("OKX_PASSPHRASE","Passphrase")):
            secret = getpass.getpass(f"OKX {label}（留空保持）: ").strip()
            if secret: set_env(key, secret)


def switch_mode() -> None:
    cfg = read_env()
    ex = exchange_name(cfg)
    if ex == "okx":
        print("1) DRY RUN  2) OKX DEMO  3) LIVE")
        c = input("选择: ").strip()
        if c == "1": set_env("DRY_RUN", "true")
        elif c == "2": set_env("DRY_RUN", "false"); set_env("OKX_DEMO", "true")
        elif c == "3": set_env("DRY_RUN", "false"); set_env("OKX_DEMO", "false")
        else: raise ValueError("无效模式")
    else:
        print("1) DRY RUN  2) LIVE")
        c = input("选择: ").strip()
        if c == "1": set_env("DRY_RUN", "true")
        elif c == "2": set_env("DRY_RUN", "false")
        else: raise ValueError("无效模式")


def configure_direction() -> None:
    cfg = read_env()
    long = input(f"允许做多? y/n [{cfg.get('ENABLE_LONGS','true')}]: ").strip().lower()
    short = input(f"允许做空? y/n [{cfg.get('ENABLE_SHORTS','true')}]: ").strip().lower()
    if long: set_env("ENABLE_LONGS", "true" if long in {"y","yes","1","true"} else "false")
    if short: set_env("ENABLE_SHORTS", "true" if short in {"y","yes","1","true"} else "false")


def configure_strategy_params() -> None:
    cfg = read_env()
    if strategy_name(cfg) == "trend_rebalance":
        sep = input(f"最小 SMA separation [{cfg.get('TREND_MIN_SEPARATION','30')}]: ").strip()
        if sep: set_env("TREND_MIN_SEPARATION", str(float(sep)))
        sl = input(f"固定 SL points [{cfg.get('TREND_SL_FIXED_POINTS','125')}]: ").strip()
        if sl: set_env("TREND_SL_FIXED_POINTS", str(float(sl)))
        print("Dynamic SMA200 TP 保持与 1.0 默认逻辑一致。")
    else:
        length = input(f"连续 K 线数量 [{cfg.get('STREAK_LENGTH','5')}]: ").strip()
        if length: set_env("STREAK_LENGTH", str(int(length)))
        window = input(f"确认窗口 bars [{cfg.get('STREAK_WAIT_BARS','15')}]: ").strip()
        if window: set_env("STREAK_WAIT_BARS", str(int(window)))
        tf = input(f"Signal timeframe 1m/5m [{cfg.get('STREAK_SIGNAL_TIMEFRAME','1m')}]: ").strip().lower()
        if tf:
            if tf not in {"1m","5m"}: raise ValueError("只能 1m 或 5m")
            set_env("STREAK_SIGNAL_TIMEFRAME", tf)
        r = input(f"TP R multiple [{cfg.get('STREAK_TP_R','1.0')}]: ").strip()
        if r: set_env("STREAK_TP_R", str(float(r)))
        print("默认 SL=terminal streak candle extreme，默认纽约时段=09:45–12:00。")


def start_bot() -> None:
    cfg = read_env()
    if mode(cfg) == "LIVE":
        print("\n即将启动 LIVE 自动交易。")
        if input("输入 START 确认: ").strip() != "START":
            print("已取消。")
            return
    subprocess.run([sys.executable, "main.py"], cwd=ROOT)


def query_funds() -> None:
    cfg = read_env()
    if exchange_name(cfg) == "hyperliquid":
        address = cfg.get("ACCOUNT_ADDRESS", "").strip()
        if not address:
            print("主账户地址未设置。")
            return
        url = "https://api.hyperliquid.xyz/info"
        dex = cfg.get("DEX", "xyz")
        def post(payload):
            r = requests.post(url, json=payload, timeout=15); r.raise_for_status(); return r.json()
        core = post({"type":"clearinghouseState","user":address})
        hip3 = post({"type":"clearinghouseState","user":address,"dex":dex})
        spot = post({"type":"spotClearinghouseState","user":address})
        print("\nHyperCore equity:", core.get("marginSummary",{}).get("accountValue","0"))
        print(f"HIP-3 {dex} equity:", hip3.get("marginSummary",{}).get("accountValue","0"))
        print("Spot:")
        for b in spot.get("balances", []):
            total = float(b.get("total") or 0)
            if total: print(f"  {b.get('coin')}: {total}")
    else:
        print("OKX 资金查询请使用交易所账户页面；交易执行器会在启动时验证账户/API。")


def main() -> None:
    ensure_env()
    while True:
        show_status()
        print(" 1) 启动机器人")
        print(" 2) 选择策略")
        print(" 3) 设置每笔仓位 / 杠杆")
        print(" 4) 设置交易所 / API 凭证")
        print(" 5) 切换 DRY RUN / DEMO / LIVE")
        print(" 6) 设置做多 / 做空方向")
        print(" 7) 设置当前策略参数")
        print(" 8) 查询资金 / 当前账户")
        print(" 0) 退出")
        choice = input("\n请选择: ").strip()
        try:
            if choice == "1": start_bot()
            elif choice == "2": configure_strategy()
            elif choice == "3": configure_position()
            elif choice == "4": configure_exchange()
            elif choice == "5": switch_mode()
            elif choice == "6": configure_direction()
            elif choice == "7": configure_strategy_params()
            elif choice == "8": query_funds()
            elif choice == "0": return
            else: print("无效选项。")
        except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            print(f"操作失败：{exc}")
        input("\n按 Enter 返回菜单...")


if __name__ == "__main__":
    main()
