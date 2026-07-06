"""Point d'entrée du Bot Égaliseur — boucle indépendante du Bot 1."""

from __future__ import annotations

import logging
import os
import time

from ultiumgrid.bot_runner import build_client_from_env
from ultiumgrid.db.models import make_session_factory

from ultium_egaliseur.engine import EgaliseurEngine

logger = logging.getLogger(__name__)


def main_loop(database_url: str | None = None) -> None:
    database_url = database_url or os.getenv(
        "DATABASE_URL", "postgresql+psycopg2://ultium:ultium@db:5432/ultiumgrid"
    )
    poll_s = float(os.getenv("EGALISEUR_POLL_S", "5"))
    SessionLocal, _ = make_session_factory(database_url)
    client = build_client_from_env()
    session = SessionLocal()
    engine = EgaliseurEngine(client, session)
    engine.ensure_config_initialized()
    logger.info(
        "Bot Égaliseur démarré poll_s=%s mode=%s",
        poll_s,
        engine.load_config().operation_mode,
    )

    while True:
        try:
            summary = engine.tick()
            if summary.get("error"):
                logger.warning("tick error: %s", summary["error"])
        except Exception:
            logger.exception("egaliseur tick failed")
            session.rollback()
        time.sleep(poll_s)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    main_loop()
