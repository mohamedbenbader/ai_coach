import psycopg2
import psycopg2.extras
import json
import os
from datetime import date, timedelta

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_conn():
    conn = psycopg2.connect(DATABASE_URL)
    return conn


def _cur(conn):
    """Curseur retournant des dicts."""
    return conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)


def init_db():
    conn = get_conn()
    c = _cur(conn)

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at DATE DEFAULT CURRENT_DATE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS profile (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            name TEXT, age INTEGER, weight_kg REAL,
            height_cm INTEGER, goal_weight_kg REAL,
            activity_level TEXT, training_days TEXT,
            goal TEXT DEFAULT 'recomposition',
            sexe TEXT DEFAULT 'homme',
            job_type TEXT DEFAULT 'bureau',
            gym_sessions_per_week INTEGER DEFAULT 3,
            extra_sport TEXT DEFAULT 'aucun',
            extra_sports TEXT DEFAULT '[]',
            fitness_level TEXT DEFAULT 'intermediaire',
            rest_days TEXT DEFAULT '[]',
            program_start_date TEXT,
            total_phases INTEGER DEFAULT 1,
            macros_training TEXT DEFAULT '{}',
            macros_rest TEXT DEFAULT '{}',
            created_at DATE DEFAULT CURRENT_DATE,
            UNIQUE(user_id)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS weight_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            weight_kg REAL,
            logged_at DATE DEFAULT CURRENT_DATE
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS meal_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            date TEXT, meal_type TEXT, description TEXT,
            calories INTEGER, protein_g REAL, carbs_g REAL, fat_g REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS training_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            date TEXT, session_type TEXT,
            completed INTEGER DEFAULT 0, notes TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_meal_plan (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            week_start TEXT, day_of_week INTEGER, meal_type TEXT,
            description TEXT, calories INTEGER,
            protein_g REAL, carbs_g REAL, fat_g REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS shopping_list (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            week_start TEXT, item TEXT, quantity TEXT, category TEXT
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS daily_plan (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            week_start TEXT, day_of_week INTEGER, meal_text TEXT,
            UNIQUE(user_id, week_start, day_of_week)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_shopping (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            week_start TEXT, shopping_text TEXT,
            UNIQUE(user_id, week_start)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS training_program (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            phase_number INTEGER NOT NULL DEFAULT 1,
            week_number INTEGER NOT NULL DEFAULT 1,
            day_of_week INTEGER,
            session_name TEXT, session_emoji TEXT, exercises_json TEXT,
            generated_at DATE DEFAULT CURRENT_DATE,
            UNIQUE(user_id, phase_number, week_number, day_of_week)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS exercise_overrides (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            date TEXT NOT NULL,
            original_name TEXT NOT NULL,
            new_name TEXT NOT NULL,
            new_sets TEXT,
            new_rest TEXT,
            UNIQUE(user_id, date, original_name)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS session_done (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            date TEXT NOT NULL,
            UNIQUE(user_id, date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS weekly_reports (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            week_start TEXT NOT NULL,
            report_text TEXT,
            UNIQUE(user_id, week_start)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS exercise_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL DEFAULT 1,
            date TEXT NOT NULL,
            exercise_name TEXT NOT NULL,
            sets_done INTEGER,
            reps TEXT,
            weight_kg REAL
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS couples (
            id SERIAL PRIMARY KEY,
            user_id_1 INTEGER NOT NULL,
            user_id_2 INTEGER NOT NULL,
            created_at DATE DEFAULT CURRENT_DATE,
            UNIQUE(user_id_1, user_id_2)
        )
    """)

    conn.commit()
    conn.close()


# ── Mode Couple ───────────────────────────────────────────────────────────────

def create_couple(user_id_1: int, user_id_2: int):
    conn = get_conn()
    c = _cur(conn)
    uid1, uid2 = min(user_id_1, user_id_2), max(user_id_1, user_id_2)
    c.execute("""
        INSERT INTO couples (user_id_1, user_id_2) VALUES (%s, %s)
        ON CONFLICT (user_id_1, user_id_2) DO NOTHING
    """, (uid1, uid2))
    conn.commit()
    conn.close()


def get_partner_user_id(user_id: int) -> int | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute("""
        SELECT CASE WHEN user_id_1 = %s THEN user_id_2 ELSE user_id_1 END AS partner_id
        FROM couples WHERE user_id_1 = %s OR user_id_2 = %s
    """, (user_id, user_id, user_id))
    row = c.fetchone()
    conn.close()
    return int(row["partner_id"]) if row else None


# ── Utilisateurs ──────────────────────────────────────────────────────────────

def create_user(username: str, password_hash: str) -> int:
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "INSERT INTO users (username, password_hash) VALUES (%s, %s) RETURNING id",
        (username, password_hash)
    )
    user_id = c.fetchone()["id"]
    conn.commit()
    conn.close()
    return user_id


def get_user_by_username(username: str) -> dict | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute("SELECT * FROM users WHERE username = %s", (username,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def get_user_by_id(user_id: int) -> dict | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute("SELECT id, username, created_at FROM users WHERE id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Profil ────────────────────────────────────────────────────────────────────

def save_profile(data: dict, user_id: int = 1):
    from profile import get_daily_targets
    conn = get_conn()
    c = _cur(conn)
    es = data.get("extra_sports", [])
    if isinstance(es, list):
        es = json.dumps(es, ensure_ascii=False)
    rest = data.get("rest_days", [])
    if not isinstance(rest, str):
        rest = json.dumps(rest)

    # Calcul des besoins nutritionnels — fait une seule fois ici, stocké en DB
    profile_for_calc = {**data, "extra_sports": json.loads(es) if isinstance(es, str) else es,
                        "rest_days": json.loads(rest) if isinstance(rest, str) else rest}
    macros_training = json.dumps(get_daily_targets(profile_for_calc, is_rest_day=False))
    macros_rest     = json.dumps(get_daily_targets(profile_for_calc, is_rest_day=True))

    c.execute("""
        INSERT INTO profile (user_id, name, age, weight_kg, height_cm, goal_weight_kg,
            activity_level, training_days, goal, sexe, job_type, gym_sessions_per_week,
            extra_sport, extra_sports, fitness_level, rest_days, macros_training, macros_rest)
        VALUES (%(user_id)s, %(name)s, %(age)s, %(weight_kg)s, %(height_cm)s, %(goal_weight_kg)s,
            %(activity_level)s, %(training_days)s, %(goal)s, %(sexe)s, %(job_type)s,
            %(gym_sessions_per_week)s, %(extra_sport)s, %(extra_sports)s, %(fitness_level)s,
            %(rest_days)s, %(macros_training)s, %(macros_rest)s)
        ON CONFLICT (user_id) DO UPDATE SET
            name = EXCLUDED.name,
            age = EXCLUDED.age,
            weight_kg = EXCLUDED.weight_kg,
            height_cm = EXCLUDED.height_cm,
            goal_weight_kg = EXCLUDED.goal_weight_kg,
            activity_level = EXCLUDED.activity_level,
            training_days = EXCLUDED.training_days,
            goal = EXCLUDED.goal,
            sexe = EXCLUDED.sexe,
            job_type = EXCLUDED.job_type,
            gym_sessions_per_week = EXCLUDED.gym_sessions_per_week,
            extra_sport = EXCLUDED.extra_sport,
            extra_sports = EXCLUDED.extra_sports,
            fitness_level = EXCLUDED.fitness_level,
            rest_days = EXCLUDED.rest_days,
            macros_training = EXCLUDED.macros_training,
            macros_rest = EXCLUDED.macros_rest
    """, {
        **data,
        "user_id": user_id,
        "goal": data.get("goal", "recomposition"),
        "sexe": data.get("sexe", "homme"),
        "activity_level": data.get("activity_level", "modere"),
        "training_days": data.get("training_days", "0,1,2,3,5,6"),
        "job_type": data.get("job_type", "bureau"),
        "gym_sessions_per_week": data.get("gym_sessions_per_week", 3),
        "extra_sport": "aucun",
        "extra_sports": es,
        "fitness_level": data.get("fitness_level", "intermediaire"),
        "rest_days": rest,
        "macros_training": macros_training,
        "macros_rest": macros_rest,
    })
    conn.commit()
    conn.close()


def get_profile(user_id: int = 1) -> dict | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute("SELECT * FROM profile WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Poids ─────────────────────────────────────────────────────────────────────

def log_weight(weight_kg: float, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    today = str(date.today())
    c.execute("DELETE FROM weight_logs WHERE logged_at = %s AND user_id = %s", (today, user_id))
    c.execute(
        "INSERT INTO weight_logs (user_id, weight_kg, logged_at) VALUES (%s, %s, %s)",
        (user_id, weight_kg, today)
    )
    conn.commit()
    conn.close()


def get_weight_logs(limit: int = 10, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT * FROM weight_logs WHERE user_id = %s ORDER BY logged_at DESC LIMIT %s",
        (user_id, limit)
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Entraînement ──────────────────────────────────────────────────────────────

def log_training(session_type: str, completed: bool = True, notes: str = "", user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    today = str(date.today())
    c.execute(
        "INSERT INTO training_logs (user_id, date, session_type, completed, notes) VALUES (%s, %s, %s, %s, %s)",
        (user_id, today, session_type, int(completed), notes)
    )
    conn.commit()
    conn.close()


# ── Plan de repas hebdomadaire ────────────────────────────────────────────────

def get_weekly_meal_plan(week_start: str, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT * FROM weekly_meal_plan WHERE user_id = %s AND week_start = %s ORDER BY day_of_week, meal_type",
        (user_id, week_start)
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_weekly_meal_plan(week_start: str, meals: list[dict], user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("DELETE FROM weekly_meal_plan WHERE user_id = %s AND week_start = %s", (user_id, week_start))
    for meal in meals:
        c.execute("""
            INSERT INTO weekly_meal_plan (user_id, week_start, day_of_week, meal_type, description, calories, protein_g, carbs_g, fat_g)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """, (user_id, week_start, meal["day_of_week"], meal["meal_type"], meal["description"],
              meal["calories"], meal["protein_g"], meal["carbs_g"], meal["fat_g"]))
    conn.commit()
    conn.close()


# ── Liste de courses ──────────────────────────────────────────────────────────

def save_shopping_list(week_start: str, items: list[dict], user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("DELETE FROM shopping_list WHERE user_id = %s AND week_start = %s", (user_id, week_start))
    for item in items:
        c.execute(
            "INSERT INTO shopping_list (user_id, week_start, item, quantity, category) VALUES (%s, %s, %s, %s, %s)",
            (user_id, week_start, item.get("item"), item.get("quantity"), item.get("category"))
        )
    conn.commit()
    conn.close()


def get_shopping_list(week_start: str, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT * FROM shopping_list WHERE user_id = %s AND week_start = %s ORDER BY category, item",
        (user_id, week_start)
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Programme d'entraînement ──────────────────────────────────────────────────

def save_training_program(days: list[dict], user_id: int = 1, phase_number: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("DELETE FROM training_program WHERE user_id = %s AND phase_number = %s", (user_id, phase_number))
    for day in days:
        week_number = int(day.get("week_number", 1))
        c.execute("""
            INSERT INTO training_program (user_id, phase_number, week_number, day_of_week, session_name, session_emoji, exercises_json)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (user_id, phase_number, week_number, day_of_week) DO UPDATE SET
                session_name = EXCLUDED.session_name,
                session_emoji = EXCLUDED.session_emoji,
                exercises_json = EXCLUDED.exercises_json
        """, (user_id, phase_number, week_number, day["day_of_week"], day["session_name"],
              day["session_emoji"], json.dumps(day["exercises"], ensure_ascii=False)))
    conn.commit()
    conn.close()


def set_program_start_date(start_date: str, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("UPDATE profile SET program_start_date = %s WHERE user_id = %s", (start_date, user_id))
    conn.commit()
    conn.close()


def set_total_phases(total_phases: int, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("UPDATE profile SET total_phases = %s WHERE user_id = %s", (total_phases, user_id))
    conn.commit()
    conn.close()


def get_max_phase(user_id: int = 1) -> int:
    conn = get_conn()
    c = _cur(conn)
    c.execute("SELECT MAX(phase_number) as mx FROM training_program WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    conn.close()
    return (row["mx"] or 1) if row else 1


def get_training_program(user_id: int = 1, week_number: int = 1, phase_number: int = 1) -> dict | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT * FROM training_program WHERE user_id = %s AND phase_number = %s AND week_number = %s ORDER BY day_of_week",
        (user_id, phase_number, week_number)
    )
    rows = c.fetchall()
    if not rows:
        c.execute("SELECT MAX(phase_number) as mx FROM training_program WHERE user_id = %s", (user_id,))
        max_row = c.fetchone()
        fallback_phase = (max_row["mx"] or 1) if max_row else 1
        if fallback_phase != phase_number:
            c.execute(
                "SELECT * FROM training_program WHERE user_id = %s AND phase_number = %s AND week_number = %s ORDER BY day_of_week",
                (user_id, fallback_phase, week_number)
            )
            rows = c.fetchall()
    if not rows:
        c.execute(
            "SELECT * FROM training_program WHERE user_id = %s AND week_number = 1 ORDER BY day_of_week LIMIT 7",
            (user_id,)
        )
        rows = c.fetchall()
    conn.close()
    if not rows:
        return None
    return {
        row["day_of_week"]: {
            "name": row["session_name"],
            "emoji": row["session_emoji"],
            "exercises": json.loads(row["exercises_json"]),
        }
        for row in rows
    }


def get_program_info(user_id: int = 1) -> dict:
    conn = get_conn()
    c = _cur(conn)
    c.execute("SELECT program_start_date, total_phases FROM profile WHERE user_id = %s", (user_id,))
    row = c.fetchone()
    c.execute("SELECT MAX(week_number) as mx FROM training_program WHERE user_id = %s", (user_id,))
    total_rows = c.fetchone()
    c.execute("SELECT MAX(phase_number) as mx FROM training_program WHERE user_id = %s", (user_id,))
    max_phase_row = c.fetchone()
    conn.close()

    total_weeks  = (total_rows["mx"] or 1) if total_rows else 1
    total_phases = int(row["total_phases"] or 1) if row else 1
    max_phase    = (max_phase_row["mx"] or 1) if max_phase_row else 1
    start_str    = row["program_start_date"] if row else None

    if start_str:
        start = date.fromisoformat(str(start_str))
        weeks_elapsed = max(0, (date.today() - start).days // 7)
        current_week  = (weeks_elapsed % total_weeks) + 1
        phase_number  = (weeks_elapsed // total_weeks) + 1
    else:
        weeks_elapsed = 0
        current_week  = 1
        phase_number  = 1

    return {
        "current_week":  current_week,
        "total_weeks":   total_weeks,
        "phase_number":  phase_number,
        "weeks_elapsed": weeks_elapsed,
        "program_start_date": str(start_str) if start_str else None,
        "total_phases":  total_phases,
        "max_phase":     max_phase,
    }


# ── Plan journalier ───────────────────────────────────────────────────────────

def get_week_start(for_date=None) -> str:
    d = for_date or date.today()
    monday = d - timedelta(days=d.weekday())
    return str(monday)


def get_next_week_start() -> str:
    monday = date.today() - timedelta(days=date.today().weekday())
    return str(monday + timedelta(weeks=1))


def save_daily_plan(week_start: str, day_of_week: int, meal_text: str, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("""
        INSERT INTO daily_plan (user_id, week_start, day_of_week, meal_text)
        VALUES (%s, %s, %s, %s)
        ON CONFLICT (user_id, week_start, day_of_week) DO UPDATE SET meal_text = EXCLUDED.meal_text
    """, (user_id, week_start, day_of_week, meal_text))
    conn.commit()
    conn.close()


def get_daily_plan(week_start: str, day_of_week: int, user_id: int = 1) -> str | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT meal_text FROM daily_plan WHERE user_id = %s AND week_start = %s AND day_of_week = %s",
        (user_id, week_start, day_of_week)
    )
    row = c.fetchone()
    conn.close()
    return row["meal_text"] if row else None


def save_weekly_shopping(week_start: str, shopping_text: str, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("""
        INSERT INTO weekly_shopping (user_id, week_start, shopping_text)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, week_start) DO UPDATE SET shopping_text = EXCLUDED.shopping_text
    """, (user_id, week_start, shopping_text))
    conn.commit()
    conn.close()


def get_weekly_shopping(week_start: str, user_id: int = 1) -> str | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT shopping_text FROM weekly_shopping WHERE user_id = %s AND week_start = %s",
        (user_id, week_start)
    )
    row = c.fetchone()
    conn.close()
    return row["shopping_text"] if row else None


# ── Overrides d'exercices ────────────────────────────────────────────────────

def save_exercise_override(log_date: str, original_name: str, new_name: str,
                           new_sets: str = None, new_rest: str = None, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("""
        INSERT INTO exercise_overrides (user_id, date, original_name, new_name, new_sets, new_rest)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (user_id, date, original_name) DO UPDATE SET
            new_name = EXCLUDED.new_name,
            new_sets = EXCLUDED.new_sets,
            new_rest = EXCLUDED.new_rest
    """, (user_id, log_date, original_name, new_name, new_sets, new_rest))
    conn.commit()
    conn.close()


def get_exercise_overrides(log_date: str, user_id: int = 1) -> dict:
    """Retourne {original_name: {new_name, new_sets, new_rest}} pour une date."""
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT * FROM exercise_overrides WHERE user_id = %s AND date = %s",
        (user_id, log_date)
    )
    rows = c.fetchall()
    conn.close()
    return {r["original_name"]: dict(r) for r in rows}


# ── Séance faite ─────────────────────────────────────────────────────────────

def toggle_session_done(log_date: str, user_id: int = 1) -> bool:
    """Toggle session done. Retourne True si maintenant done, False si retiré."""
    conn = get_conn()
    c = _cur(conn)
    c.execute("SELECT id FROM session_done WHERE user_id = %s AND date = %s", (user_id, log_date))
    existing = c.fetchone()
    if existing:
        c.execute("DELETE FROM session_done WHERE user_id = %s AND date = %s", (user_id, log_date))
        done = False
    else:
        c.execute("INSERT INTO session_done (user_id, date) VALUES (%s, %s)", (user_id, log_date))
        done = True
    conn.commit()
    conn.close()
    return done


def get_done_dates(week_start: str, user_id: int = 1) -> set:
    """Retourne l'ensemble des dates où une séance a été marquée 'faite' pour la semaine."""
    conn = get_conn()
    c = _cur(conn)
    week_end = str(date.fromisoformat(week_start) + timedelta(days=6))
    c.execute(
        "SELECT date FROM session_done WHERE user_id = %s AND date >= %s AND date <= %s",
        (user_id, week_start, week_end)
    )
    rows = c.fetchall()
    conn.close()
    return {r["date"] for r in rows}


# ── Bilan hebdomadaire ────────────────────────────────────────────────────────

def save_weekly_report(week_start: str, report_text: str, user_id: int = 1):
    conn = get_conn()
    c = _cur(conn)
    c.execute("""
        INSERT INTO weekly_reports (user_id, week_start, report_text)
        VALUES (%s, %s, %s)
        ON CONFLICT (user_id, week_start) DO UPDATE SET report_text = EXCLUDED.report_text
    """, (user_id, week_start, report_text))
    conn.commit()
    conn.close()


def get_weekly_report(week_start: str, user_id: int = 1) -> str | None:
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT report_text FROM weekly_reports WHERE user_id = %s AND week_start = %s",
        (user_id, week_start)
    )
    row = c.fetchone()
    conn.close()
    return row["report_text"] if row else None


# ── Logs de séance (exercices réels) ─────────────────────────────────────────

def save_exercise_logs(log_date: str, exercises: list[dict], user_id: int = 1):
    """Remplace les logs du jour par les nouvelles entrées."""
    conn = get_conn()
    c = _cur(conn)
    c.execute("DELETE FROM exercise_logs WHERE user_id = %s AND date = %s", (user_id, log_date))
    for ex in exercises:
        c.execute("""
            INSERT INTO exercise_logs (user_id, date, exercise_name, sets_done, reps, weight_kg)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (user_id, log_date, ex["exercise_name"],
              ex.get("sets_done"), ex.get("reps"), ex.get("weight_kg")))
    conn.commit()
    conn.close()


def get_exercise_logs(log_date: str, user_id: int = 1) -> list[dict]:
    conn = get_conn()
    c = _cur(conn)
    c.execute(
        "SELECT * FROM exercise_logs WHERE user_id = %s AND date = %s ORDER BY id",
        (user_id, log_date)
    )
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_phase_exercise_summary(phase_start: str, phase_end: str, user_id: int = 1) -> list[dict]:
    """Aggrège les perfs par exercice sur une plage de dates (une phase = ~4 semaines)."""
    conn = get_conn()
    c = _cur(conn)
    c.execute("""
        SELECT exercise_name,
               ROUND(AVG(weight_kg)::numeric, 1) as avg_weight,
               ROUND(AVG(sets_done)::numeric, 0) as avg_sets,
               COUNT(*) as sessions
        FROM exercise_logs
        WHERE user_id = %s AND date >= %s AND date < %s AND weight_kg > 0
        GROUP BY exercise_name
        ORDER BY sessions DESC
        LIMIT 20
    """, (user_id, phase_start, phase_end))
    rows = c.fetchall()
    conn.close()
    return [dict(r) for r in rows]
