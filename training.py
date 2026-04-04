"""
Programme d'entraînement.
Généré par IA à la création du profil, stocké en DB.
"""
from datetime import date

DAYS_FR = ["Lundi", "Mardi", "Mercredi", "Jeudi", "Vendredi", "Samedi", "Dimanche"]


def _get_program() -> dict:
    """Lit le programme depuis la DB. Retourne {} si aucun programme n'existe."""
    from db import get_training_program
    db_program = get_training_program()
    return db_program if db_program else {}


def get_today_session() -> dict:
    weekday = date.today().weekday()
    return get_session_for_day(weekday)


def get_session_for_day(weekday: int) -> dict:
    program = _get_program()
    session = program.get(weekday, {"name": "REPOS", "emoji": "😴", "exercises": []})
    return {"weekday": weekday, **session}


def is_rest_day(weekday: int = None) -> bool:
    if weekday is None:
        weekday = date.today().weekday()
    program = _get_program()
    session = program.get(weekday, {})
    return session.get("name") == "REPOS" or not session.get("exercises")


def format_session_message(session: dict) -> str:
    if session.get("name") == "REPOS" or not session.get("exercises"):
        return "😴 *Aujourd'hui c'est REPOS* — récupération physique et mentale."

    lines = [f"{session['emoji']} *{session['name']}*\n"]
    for ex in session["exercises"]:
        rest = ex.get("rest", "")
        rest_str = f" (repos {rest})" if rest else ""
        lines.append(f"• {ex['name']} — {ex['sets']}{rest_str}")

    return "\n".join(lines)


def get_week_summary() -> str:
    program = _get_program()
    lines = ["📅 *Programme de la semaine :*\n"]
    for day_num in range(7):
        session = program.get(day_num, {})
        day_name = DAYS_FR[day_num]
        emoji = session.get("emoji", "")
        name = session.get("name", "")
        lines.append(f"*{day_name}* — {emoji} {name}")
    return "\n".join(lines)
