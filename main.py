import logging

from app.bot import TradingBot
from app.config import Config


def main() -> None:
    try:
        cfg = Config.load()
        logging.basicConfig(
            level=getattr(logging, cfg.log_level, logging.INFO),
            format="%(asctime)s %(levelname)s %(name)s | %(message)s",
        )
        TradingBot(cfg).run()
    except (ValueError, RuntimeError) as exc:
        print(f"启动失败：{exc}")
        raise SystemExit(2)


if __name__ == "__main__":
    main()
