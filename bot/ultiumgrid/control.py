"""Canal de commandes partagé bot ↔ backend via table bot_state."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from ultiumgrid.db.models import BotState, utcnow


def push_command(session: Session, name: str, payload: dict[str, Any] | None = None) -> None:
    row = session.query(BotState).filter(BotState.key == "commands").first()
    commands = list((row.value_json or {}).get("queue", [])) if row else []
    commands.append({"name": name, "payload": payload or {}, "at": utcnow().isoformat()})
    data = {"queue": commands}
    if not row:
        session.add(BotState(key="commands", value_json=data))
    else:
        row.value_json = data
        row.updated_at = utcnow()
    session.commit()


def pop_commands(session: Session) -> list[dict[str, Any]]:
    row = session.query(BotState).filter(BotState.key == "commands").first()
    if not row:
        return []
    commands = list((row.value_json or {}).get("queue", []))
    row.value_json = {"queue": []}
    row.updated_at = utcnow()
    session.commit()
    return commands


def read_main_state(session: Session) -> dict[str, Any]:
    row = session.query(BotState).filter(BotState.key == "main").first()
    return dict(row.value_json) if row and row.value_json else {}
