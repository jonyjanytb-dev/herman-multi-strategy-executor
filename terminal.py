from __future__ import annotations

import getpass
import json
import shutil
import subprocess
import sys
from pathlib import Path

import requests
from dotenv import dotenv_values

from app.config import DEFAULT_OKX_INST_ID
from app.okx_client import OKXClient

ROOT = Path(__file__).resolve().parent
ENV_PATH = ROOT / ".env"
ENV_EXAMPLE = ROOT / ".env.example"


def ensure_env() -> None:
    if not ENV_PATH.exists():
        shutil.copyfile(ENV_EXAMPLE, ENV_PATH)
        print("已创建 .env；默认 DRY_RUN=true。")


def read_env() -> dict[str, str]:
    ensure_env()
    return {k: str(v or "") for k, v in dotenv_values(ENV_PATH).items()}


def set_env(key: str, value: str) -> None:
    ensure_env()
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines()
    prefix = key + "="
    found = False
    out = []
    for line in lines:
        if line.startswith(prefix):
            out.append(prefix + value)
            found = True
        else:
            out.append(line)
    if not found:
        out.append(prefix + value)
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


def b(cfg, key, default="false") -> bool:
    return cfg.get(key, default).strip().lower() in {"1", "true", "yes", "on"}


def selected_exchange(cfg):
    return cfg.get("EXCHANGE", "hyperliquid").strip().lower() or "hyperliquid"


def selected_strategy(cfg):
    return cfg.get("STRATEGY", "trend_rebalance").strip().lower() or "trend_rebalance"


def strategy_label(cfg):
    return "1.1 Streak Failure Reversal" if selected_strategy(cfg) == "streak_failure" else "1.0 Trend Rebalance Map"


def mode(cfg):
    if b(cfg, "DRY_RUN", "true"):
        return "dry_run"
    if selected_exchange(cfg) == "okx" and b(cfg, "OKX_DEMO", "true"):
        return "demo"
    return "live"


def symbol(cfg):
    return cfg.get("OKX_INST_ID", DEFAULT_OKX_INST_ID) if selected_exchange(cfg) == "okx" else cfg.get("COIN", "xyz:XYZ100")


def mask(v: str) -> str:
    if not v:
        return "未设置"
    return v if len(v) <= 12 else f"{v[:6]}...{v[-4:]}"


def default_state_path(cfg, strategy=None, exchange=None):
    strategy = strategy or selected_strategy(cfg)
    exchange = exchange or selected_exchange(cfg)
    return f"runtime/state-{exchange}-{strategy}.json"


def show_status() -> None:
    cfg = read_env()
    m = mode(cfg)
    notional = float(cfg.get("ORDER_NOTIONAL_USDC", "100") or 100)
    leverage = int(cfg.get("LEVERAGE", "0") or 0)
    print("\n" + "=" * 66)
    print(" Herman Multi Strategy Executor · Hyperliquid / OKX")
    print("=" * 66)
    print(f" 策略        : {strategy_label(cfg)}")
    print(f" 交易所      : {'OKX' if selected_exchange(cfg) == 'okx' else 'Hyperliquid'}")
    mode_text = {"dry_run": "DRY RUN", "demo": "OKX DEMO", "live": "LIVE（真实下单）"}[m]
    print(f" 模式        : {mode_text}")
    print(f" 市场        : {symbol(cfg)}")
    print(f" 周期        : 1m")
    print(f" 每笔名义仓位: {notional:.2f}")
    print(f" 杠杆        : {leverage}x" if leverage else " 杠杆        : 不自动修改")
    if selected_exchange(cfg) == "hyperliquid":
        print(f" 主账户      : {mask(cfg.get('ACCOUNT_ADDRESS',''))}")
        print(f" API Wallet  : {'已设置' if cfg.get('API_PRIVATE_KEY') else '未设置'}")
    else:
        ready = all(cfg.get(k) for k in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE"))
        print(f" OKX API     : {'已设置' if ready else '未设置'}")
    print(f" 做多 / 做空: {'是' if b(cfg,'ENABLE_LONGS','true') else '否'} / {'是' if b(cfg,'ENABLE_SHORTS','true') else '否'}")
    if selected_strategy(cfg) == "trend_rebalance":
        print(f" TP          : {cfg.get('TP_MODE','200 SMA')} / {cfg.get('SMA_TARGET_BEHAVIOUR','Dynamic')}")
        print(f" SL          : {cfg.get('SL_MODE','Fixed Points')} / {cfg.get('SL_FIXED_POINTS','125')} points")
    else:
        print(f" Signal TF   : {cfg.get('STREAK_SIGNAL_TIMEFRAME','1m')} | streak={cfg.get('STREAK_LENGTH','5')} | window={cfg.get('STREAK_WAIT_BARS','15')}")
        print(f" TP / SL     : {cfg.get('STREAK_TP_MODE','R multiple')} / {cfg.get('STREAK_SL_MODE','Terminal streak candle extreme')}")
        print(f" Session ET  : {cfg.get('STREAK_SESSION_START','09:45')}-{cfg.get('STREAK_SESSION_END','12:00')} | hard flat {cfg.get('STREAK_HARD_FLAT','16:00')}")
    print(f" State       : {cfg.get('STATE_PATH') or default_state_path(cfg)}")
    print("=" * 66)


def has_open_position(cfg: dict[str, str]) -> bool:
    if selected_exchange(cfg) == "hyperliquid":
        address = cfg.get("ACCOUNT_ADDRESS", "").strip()
        if not address:
            return False
        url = "https://api.hyperliquid.xyz/info" if cfg.get("NETWORK", "mainnet") == "mainnet" else "https://api.hyperliquid-testnet.xyz/info"
        r = requests.post(url, json={"type": "clearinghouseState", "user": address, "dex": cfg.get("DEX", "xyz")}, timeout=10)
        r.raise_for_status()
        for wrapper in r.json().get("assetPositions", []):
            p = wrapper.get("position", {})
            if p.get("coin") == cfg.get("COIN", "xyz:XYZ100") and abs(float(p.get("szi") or 0)) > 0:
                return True
        return False
    if not all(cfg.get(k) for k in ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")):
        return False
    client = OKXClient(
        base_url=cfg.get("OKX_BASE_URL", "https://www.okx.com"),
        api_key=cfg["OKX_API_KEY"], secret_key=cfg["OKX_SECRET_KEY"], passphrase=cfg["OKX_PASSPHRASE"],
        demo=b(cfg, "OKX_DEMO", "true"),
    )
    rows = client.get_private("/api/v5/account/positions", {"instId": cfg.get("OKX_INST_ID", DEFAULT_OKX_INST_ID)})
    return any(abs(float(x.get("pos") or 0)) > 0 for x in rows)


def choose_strategy() -> None:
    cfg = read_env()
    print("\n请选择策略：")
    print(" 1) 1.0 Trend Rebalance Map      — SMA50/SMA200 拉伸后的均值回归")
    print(" 2) 1.1 Streak Failure Reversal  — 连续K线后等待结构失败反转")
    raw = input("选择 [1/2, Enter=保持]: ").strip()
    if not raw:
        return
    if raw not in {"1", "2"}:
        raise ValueError("只能选择 1 或 2")
    new_strategy = "trend_rebalance" if raw == "1" else "streak_failure"
    if new_strategy == selected_strategy(cfg):
        print("策略未变化。")
        return
    try:
        if has_open_position(cfg):
            raise RuntimeError("检测到该市场仍有真实持仓，禁止切换策略。请先处理持仓。")
    except requests.RequestException as exc:
        if mode(cfg) == "live":
            raise RuntimeError(f"LIVE 模式下无法确认账户是否空仓，拒绝切换策略：{exc}") from exc
    set_env("STRATEGY", new_strategy)
    set_env("STATE_PATH", default_state_path(cfg, strategy=new_strategy))
    set_env("DRY_RUN", "true")
    print(f"已切换到 {'1.0 Trend Rebalance Map' if new_strategy == 'trend_rebalance' else '1.1 Streak Failure Reversal'}。")
    print("安全措施：已自动切回 DRY RUN；确认参数后再手动切回 LIVE。")


def configure_position() -> None:
    cfg = read_env()
    n = input(f"每笔名义仓位 [{cfg.get('ORDER_NOTIONAL_USDC','100')}]: ").strip()
    l = input(f"杠杆（0=不自动修改） [{cfg.get('LEVERAGE','0')}]: ").strip()
    if n:
        if float(n) <= 0: raise ValueError("仓位必须 > 0")
        set_env("ORDER_NOTIONAL_USDC", n)
    if l:
        if int(l) < 0: raise ValueError("杠杆不能 < 0")
        set_env("LEVERAGE", l)


def configure_exchange() -> None:
    cfg = read_env()
    raw = input("交易所 [1=Hyperliquid, 2=OKX, Enter=保持]: ").strip()
    ex = {"1":"hyperliquid", "2":"okx"}.get(raw, selected_exchange(cfg))
    if raw not in {"", "1", "2"}: raise ValueError("无效交易所选项")
    if ex != selected_exchange(cfg):
        set_env("EXCHANGE", ex)
        set_env("STATE_PATH", default_state_path(cfg, exchange=ex))
        set_env("DRY_RUN", "true")
        cfg = read_env()
    if ex == "hyperliquid":
        addr = input(f"主账户地址 [{mask(cfg.get('ACCOUNT_ADDRESS',''))}]: ").strip()
        if addr: set_env("ACCOUNT_ADDRESS", addr)
        print("输入 API Wallet 私钥时不会显示字符；留空保持。")
        key = getpass.getpass("API Wallet Private Key: ").strip()
        if key: set_env("API_PRIVATE_KEY", key)
    else:
        inst = input(f"OKX 永续合约 [{cfg.get('OKX_INST_ID',DEFAULT_OKX_INST_ID)}]: ").strip().upper()
        if inst: set_env("OKX_INST_ID", inst)
        print("输入 OKX Read+Trade（无 Withdraw）凭证；留空保持。")
        for key, label in (("OKX_API_KEY","API Key"),("OKX_SECRET_KEY","Secret"),("OKX_PASSPHRASE","Passphrase")):
            val = getpass.getpass(f"{label}: ").strip()
            if val: set_env(key, val)


def switch_mode() -> None:
    cfg = read_env()
    print("1) DRY RUN   2) DEMO(仅OKX)   3) LIVE")
    raw = input("选择: ").strip()
    if raw == "1":
        set_env("DRY_RUN", "true")
    elif raw == "2":
        if selected_exchange(cfg) != "okx": raise ValueError("DEMO 仅适用于 OKX")
        set_env("DRY_RUN", "false"); set_env("OKX_DEMO", "true")
    elif raw == "3":
        confirm = input("将启用真实下单。输入 LIVE 确认: ").strip()
        if confirm != "LIVE":
            print("未切换。")
            return
        set_env("DRY_RUN", "false")
        if selected_exchange(cfg) == "okx": set_env("OKX_DEMO", "false")
    else:
        raise ValueError("无效模式")


def configure_direction() -> None:
    cfg = read_env()
    raw = input(f"方向 [1=双向,2=仅多,3=仅空] 当前={cfg.get('ENABLE_LONGS','true')}/{cfg.get('ENABLE_SHORTS','true')}: ").strip()
    if raw == "1": a,bv="true","true"
    elif raw == "2": a,bv="true","false"
    elif raw == "3": a,bv="false","true"
    else: raise ValueError("无效方向")
    set_env("ENABLE_LONGS", a); set_env("ENABLE_SHORTS", bv)


def configure_strategy_params() -> None:
    cfg = read_env()
    if selected_strategy(cfg) == "trend_rebalance":
        sep = input(f"最小 SMA 分离 points [{cfg.get('MIN_SEPARATION','30')}]: ").strip()
        sl = input(f"固定 SL points [{cfg.get('SL_FIXED_POINTS','125')}]: ").strip()
        if sep: set_env("MIN_SEPARATION", sep)
        if sl: set_env("SL_FIXED_POINTS", sl)
        dyn = input(f"200 SMA TP [1=Dynamic,2=Locked] 当前={cfg.get('SMA_TARGET_BEHAVIOUR','Dynamic')}: ").strip()
        if dyn == "1": set_env("SMA_TARGET_BEHAVIOUR", "Dynamic")
        elif dyn == "2": set_env("SMA_TARGET_BEHAVIOUR", "Locked at Entry")
    else:
        tf = input(f"Signal timeframe [1=1m,2=5m] 当前={cfg.get('STREAK_SIGNAL_TIMEFRAME','1m')}: ").strip()
        if tf == "1": set_env("STREAK_SIGNAL_TIMEFRAME", "1m")
        elif tf == "2": set_env("STREAK_SIGNAL_TIMEFRAME", "5m")
        n = input(f"连续 K 数 [{cfg.get('STREAK_LENGTH','5')}]: ").strip()
        w = input(f"确认窗口 bars [{cfg.get('STREAK_WAIT_BARS','15')}]: ").strip()
        r = input(f"TP R 倍数 [{cfg.get('STREAK_TP_R','1.0')}]: ").strip()
        if n: set_env("STREAK_LENGTH", n)
        if w: set_env("STREAK_WAIT_BARS", w)
        if r: set_env("STREAK_TP_R", r)
        print("其它高级参数可直接编辑 .env；默认与原 Pine 一致。")


def query_funds() -> None:
    cfg = read_env()
    if selected_exchange(cfg) == "hyperliquid":
        addr = cfg.get("ACCOUNT_ADDRESS", "").strip()
        if not addr: raise ValueError("未设置 ACCOUNT_ADDRESS")
        url = "https://api.hyperliquid.xyz/info" if cfg.get("NETWORK","mainnet") == "mainnet" else "https://api.hyperliquid-testnet.xyz/info"
        def post(payload):
            r=requests.post(url,json=payload,timeout=10); r.raise_for_status(); return r.json()
        core=post({"type":"clearinghouseState","user":addr})
        hip=post({"type":"clearinghouseState","user":addr,"dex":cfg.get("DEX","xyz")})
        spot=post({"type":"spotClearinghouseState","user":addr})
        print(f"\nHyperliquid · {mask(addr)}")
        for name, st in (("HyperCore",core),("HIP-3",hip)):
            ms=st.get("marginSummary",{})
            print(f" {name}: equity={ms.get('accountValue','0')} withdrawable={st.get('withdrawable','0')}")
        for row in spot.get("balances",[]):
            if float(row.get("total") or 0) != 0:
                print(f" Spot {row.get('coin')}: total={row.get('total')} hold={row.get('hold','0')}")
    else:
        c=OKXClient(base_url=cfg.get("OKX_BASE_URL","https://www.okx.com"), api_key=cfg.get("OKX_API_KEY",""), secret_key=cfg.get("OKX_SECRET_KEY",""), passphrase=cfg.get("OKX_PASSPHRASE",""), demo=b(cfg,"OKX_DEMO","true"))
        bal=c.get_private("/api/v5/account/balance")
        pos=c.get_private("/api/v5/account/positions", {"instId":cfg.get("OKX_INST_ID",DEFAULT_OKX_INST_ID)})
        print(json.dumps({"balance":bal,"positions":pos}, ensure_ascii=False, indent=2))


def start_bot() -> None:
    cfg = read_env()
    show_status()
    if mode(cfg) == "live":
        if input("即将启动 LIVE 自动交易。输入 START 确认: ").strip() != "START":
            print("已取消。")
            return
    print("按 Ctrl+C 停止机器人并返回菜单。\n")
    subprocess.run([sys.executable, str(ROOT / "main.py")], check=False)


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
        print(" 7) 设置策略参数")
        print(" 8) 刷新状态")
        print(" 9) 查询资金 / 当前持仓")
        print(" 0) 退出")
        choice = input("\n请选择: ").strip()
        try:
            if choice == "1": start_bot()
            elif choice == "2": choose_strategy()
            elif choice == "3": configure_position()
            elif choice == "4": configure_exchange()
            elif choice == "5": switch_mode()
            elif choice == "6": configure_direction()
            elif choice == "7": configure_strategy_params()
            elif choice == "8": continue
            elif choice == "9": query_funds()
            elif choice == "0": return
            else: print("无效选项。")
        except (ValueError, RuntimeError, OSError, requests.RequestException) as exc:
            print(f"操作失败：{exc}")
        input("\n按 Enter 返回菜单...")


if __name__ == "__main__":
    main()
