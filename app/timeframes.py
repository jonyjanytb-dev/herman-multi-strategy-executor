from __future__ import annotations


# Lighter supports more resolutions, but these five keep the interactive bot
# practical for intraday execution and bounded historical replays.
LIGHTER_INTERVAL_SECONDS: dict[str, int] = {
    "1m": 60,
    "5m": 5 * 60,
    "15m": 15 * 60,
    "30m": 30 * 60,
    "1h": 60 * 60,
}

# Match the AW model's automatic higher-timeframe pairing.
AW_AUTO_HTF_MINUTES: dict[str, int] = {
    "1m": 15,
    "5m": 60,
    "15m": 240,
    "30m": 360,
    "1h": 1440,
}


def lighter_interval_seconds(interval: str) -> int:
    try:
        return LIGHTER_INTERVAL_SECONDS[interval]
    except KeyError as exc:
        choices = ", ".join(LIGHTER_INTERVAL_SECONDS)
        raise ValueError(f"Unsupported Lighter interval {interval!r}; choose {choices}") from exc


def interval_minutes(interval: str) -> int:
    seconds = lighter_interval_seconds(interval)
    if seconds % 60:
        raise ValueError(f"Interval {interval!r} is not minute aligned")
    return seconds // 60
