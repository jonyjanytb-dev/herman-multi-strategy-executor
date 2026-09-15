from pathlib import Path

from dotenv import dotenv_values

from app.credentials import repair_from_legacy, valid_eth_address, valid_private_key


def test_validators():
    assert valid_eth_address("0x" + "1" * 40)
    assert not valid_eth_address("0x1234")
    assert valid_private_key("2" * 64)
    assert valid_private_key("0x" + "a" * 64)
    assert not valid_private_key("not-a-key")


def test_repairs_invalid_shared_hyperliquid_credentials(tmp_path: Path):
    current = tmp_path / "new.env"
    legacy = tmp_path / "old.env"

    current.write_text(
        "STRATEGY=streak_failure\n"
        "ACCOUNT_ADDRESS=bad\n"
        "API_PRIVATE_KEY=not-hex\n",
        encoding="utf-8",
    )
    legacy.write_text(
        f"ACCOUNT_ADDRESS=0x{'1' * 40}\n"
        f"API_PRIVATE_KEY={'2' * 64}\n",
        encoding="utf-8",
    )

    repaired = repair_from_legacy(current, legacy)
    values = dotenv_values(current)

    assert repaired == ["Hyperliquid"]
    assert values["STRATEGY"] == "streak_failure"
    assert values["ACCOUNT_ADDRESS"] == "0x" + "1" * 40
    assert values["API_PRIVATE_KEY"] == "2" * 64


def test_does_not_overwrite_valid_current_credentials(tmp_path: Path):
    current = tmp_path / "new.env"
    legacy = tmp_path / "old.env"

    current.write_text(
        f"ACCOUNT_ADDRESS=0x{'3' * 40}\n"
        f"API_PRIVATE_KEY={'4' * 64}\n",
        encoding="utf-8",
    )
    legacy.write_text(
        f"ACCOUNT_ADDRESS=0x{'1' * 40}\n"
        f"API_PRIVATE_KEY={'2' * 64}\n",
        encoding="utf-8",
    )

    repaired = repair_from_legacy(current, legacy)
    values = dotenv_values(current)

    assert repaired == []
    assert values["ACCOUNT_ADDRESS"] == "0x" + "3" * 40
    assert values["API_PRIVATE_KEY"] == "4" * 64
