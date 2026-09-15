from __future__ import annotations

import re
from pathlib import Path
from typing import Mapping

from dotenv import dotenv_values


ETH_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
PRIVATE_KEY_RE = re.compile(r"^(?:0x)?[0-9a-fA-F]{64}$")


def valid_eth_address(value: str) -> bool:
    return bool(ETH_ADDRESS_RE.fullmatch((value or "").strip()))


def valid_private_key(value: str) -> bool:
    return bool(PRIVATE_KEY_RE.fullmatch((value or "").strip()))


def _read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = dotenv_values(path)
    return {k: str(v or "").strip() for k, v in raw.items()}


def _write_values(path: Path, updates: Mapping[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    remaining = dict(updates)
    out: list[str] = []

    for line in lines:
        if "=" not in line or line.lstrip().startswith("#"):
            out.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)

    for key, value in remaining.items():
        out.append(f"{key}={value}")

    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def repair_from_legacy(current_env: Path, legacy_env: Path) -> list[str]:
    """Repair the new shared .env from the previous executor's local .env.

    Existing valid credentials are never overwritten. Secrets stay on the local
    machine; this function only copies values between two local .env files.
    Returns the names of credential groups repaired, never secret values.
    """
    if not legacy_env.exists() or not current_env.exists():
        return []

    current = _read(current_env)
    legacy = _read(legacy_env)
    updates: dict[str, str] = {}
    repaired: list[str] = []

    legacy_addr = legacy.get("ACCOUNT_ADDRESS", "")
    legacy_key = legacy.get("API_PRIVATE_KEY", "")
    if (
        (not valid_eth_address(current.get("ACCOUNT_ADDRESS", ""))
         or not valid_private_key(current.get("API_PRIVATE_KEY", "")))
        and valid_eth_address(legacy_addr)
        and valid_private_key(legacy_key)
    ):
        updates["ACCOUNT_ADDRESS"] = legacy_addr
        updates["API_PRIVATE_KEY"] = legacy_key
        repaired.append("Hyperliquid")

    okx_keys = ("OKX_API_KEY", "OKX_SECRET_KEY", "OKX_PASSPHRASE")
    if not all(current.get(k, "") for k in okx_keys) and all(legacy.get(k, "") for k in okx_keys):
        for key in okx_keys:
            updates[key] = legacy[key]
        repaired.append("OKX")

    if updates:
        _write_values(current_env, updates)
    return repaired


def repair_default_paths() -> list[str]:
    root = Path(__file__).resolve().parents[1]
    current = root / ".env"
    legacy = Path.home() / "hyperliquid-herman-executor" / ".env"
    return repair_from_legacy(current, legacy)


if __name__ == "__main__":
    repaired = repair_default_paths()
    if repaired:
        print("✓ 已从旧项目本机 .env 修复共享凭证：" + ", ".join(repaired))
        print("✓ 私钥 / Secret 未显示，也不会上传 GitHub")
