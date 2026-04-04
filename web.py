"""
Interface web Flask — application de coaching sportif.
"""
import os
import traceback
import logging
from flask import Flask, render_template, request, jsonify, session, redirect, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import json
import psycopg2.extras
import db
import profile as prof
from profile import parse_extra_sports
from training import is_rest_day, DAYS_FR

_REPOS_SESSION = {"name": "REPOS", "emoji": "😴", "exercises": []}
from datetime import date, timedelta
import calendar

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")


# ── PWA ────────────────────────────────────────────────────────────────────────
@app.route('/sw.js')
def service_worker():
    response = send_from_directory(app.static_folder, 'sw.js')
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Service-Worker-Allowed'] = '/'
    return response


@app.route('/manifest.json')
def manifest():
    return send_from_directory(app.static_folder, 'manifest.json')


# ── Auth helpers ───────────────────────────────────────────────────────────────
def current_user_id() -> int:
    return session["user_id"]


@app.before_request
def require_login():
    public = {"login_page", "api_login", "api_register", "static"}
    if request.endpoint in public:
        return
    if not session.get("user_id"):
        if request.path.startswith("/api/"):
            return jsonify({"ok": False, "error": "Non authentifié"}), 401
        return redirect("/login")


# ── Auth routes ────────────────────────────────────────────────────────────────
@app.route("/login")
def login_page():
    if session.get("user_id"):
        return redirect("/")
    return render_template("login.html")


@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "Identifiants manquants"}), 400
    if len(username) < 3:
        return jsonify({"ok": False, "error": "Nom d'utilisateur trop court (min 3 caractères)"}), 400
    if len(password) < 6:
        return jsonify({"ok": False, "error": "Mot de passe trop court (min 6 caractères)"}), 400
    if db.get_user_by_username(username):
        return jsonify({"ok": False, "error": "Nom d'utilisateur déjà pris"}), 409
    hashed = generate_password_hash(password)
    user_id = db.create_user(username, hashed)
    session["user_id"] = user_id
    session["username"] = username
    return jsonify({"ok": True, "username": username})


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json() or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", "")).strip()
    if not username or not password:
        return jsonify({"ok": False, "error": "Identifiants manquants"}), 400
    user = db.get_user_by_username(username)
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"ok": False, "error": "Identifiants invalides"}), 401
    session["user_id"] = user["id"]
    session["username"] = user["username"]
    return jsonify({"ok": True, "username": user["username"]})


@app.route("/api/auth/logout", methods=["POST"])
def api_logout():
    session.clear()
    return jsonify({"ok": True})


@app.route("/api/me")
def api_me():
    uid = session.get("user_id")
    username = session.get("username")
    if not uid:
        return jsonify({"user": None}), 401
    return jsonify({"user": {"id": uid, "username": username}})


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/profile", methods=["GET"])
def get_profile():
    uid = current_user_id()
    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"profile": None})

    p["extra_sports"] = parse_extra_sports(p.get("extra_sports"))

    weekday = date.today().weekday()
    rest_day = is_rest_day(weekday)
    macros = prof.get_daily_targets(p, is_rest_day=rest_day)

    weight_logs = db.get_weight_logs(limit=30, user_id=uid)
    current_weight = weight_logs[0]["weight_kg"] if weight_logs else p["weight_kg"]
    start_weight = weight_logs[-1]["weight_kg"] if weight_logs else p["weight_kg"]

    return jsonify({
        "profile": p,
        "macros": macros,
        "weight_logs": weight_logs,
        "current_weight": current_weight,
        "start_weight": start_weight,
    })


@app.route("/api/profile", methods=["POST"])
def save_profile_route():
    uid = current_user_id()
    data = request.get_json()
    required = ["name", "age", "weight_kg", "height_cm", "goal_weight_kg"]
    if not all(k in data for k in required):
        return jsonify({"ok": False, "error": "Champs manquants"}), 400

    try:
        valid_jobs    = ("bureau", "maison", "debout", "mouvement", "physique")
        valid_sports  = ("running", "velo", "natation", "yoga", "pilates",
                         "football", "arts_martiaux", "danse", "crossfit", "tennis")
        valid_levels  = ("debutant", "intermediaire", "avance", "expert")

        # Valider et nettoyer la liste des sports additionnels
        extra_sports_raw = parse_extra_sports(data.get("extra_sports", []))
        validated_sports = []
        for item in (extra_sports_raw or []):
            if isinstance(item, dict):
                sport = item.get("sport", "")
                sessions = int(item.get("sessions", 1))
                if sport in valid_sports:
                    validated_sports.append({"sport": sport, "sessions": max(1, min(7, sessions))})

        # Valider rest_days : liste d'entiers 0-6
        from profile import parse_rest_days
        validated_rest = parse_rest_days(data.get("rest_days", []))
        # Limite : max 7 - gym_sessions jours de repos
        max_rest = 7 - max(1, min(7, int(data.get("gym_sessions_per_week", 3))))
        validated_rest = validated_rest[:max_rest]

        payload = {
            "name":                  str(data["name"]).strip(),
            "age":                   int(data["age"]),
            "weight_kg":             float(data["weight_kg"]),
            "height_cm":             int(data["height_cm"]),
            "goal_weight_kg":        float(data["goal_weight_kg"]),
            "activity_level":        data.get("activity_level", "modere"),
            "training_days":         data.get("training_days", "0,1,2,3,5,6"),
            "goal":                  data.get("goal", "recomposition"),
            "sexe":                  data.get("sexe", "homme") if data.get("sexe") in ("homme", "femme") else "homme",
            "job_type":              data.get("job_type", "bureau") if data.get("job_type") in valid_jobs else "bureau",
            "gym_sessions_per_week": max(1, min(7, int(data.get("gym_sessions_per_week", 3)))),
            "extra_sports":          validated_sports,
            "fitness_level":         data.get("fitness_level", "intermediaire") if data.get("fitness_level") in valid_levels else "intermediaire",
            "rest_days":             validated_rest,
        }
        db.save_profile(payload, user_id=uid)
        db.log_weight(payload["weight_kg"], user_id=uid)
        return jsonify({"ok": True, "generating_program": True})
    except (ValueError, TypeError) as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/profile", methods=["DELETE"])
def delete_profile_route():
    """Supprime le profil complet + programme de l'utilisateur → repart de zéro."""
    uid = current_user_id()
    conn = db.get_conn()
    c = conn.cursor()
    for table in ("profile", "training_program", "daily_plan", "weekly_meal_plan",
                  "shopping_list", "weekly_shopping", "weight_logs"):
        try:
            c.execute(f"DELETE FROM {table} WHERE user_id = %s", (uid,))
        except Exception:
            pass
    conn.commit()
    conn.close()
    return jsonify({"ok": True})


@app.route("/api/generate_program", methods=["POST"])
def generate_program():
    """Génère et sauvegarde la Phase 1 du programme d'entraînement (4 semaines) via IA."""
    from ai import generate_phase
    from profile import weeks_to_goal

    uid = current_user_id()
    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    data = request.get_json() or {}
    if data.get("goal"):
        p["goal"] = data["goal"]

    try:
        # Supprimer les repas et courses des semaines futures (garder la semaine courante)
        current_week = db.get_week_start()
        conn = db.get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM daily_plan WHERE user_id = %s AND week_start > %s", (uid, current_week))
        c.execute("DELETE FROM weekly_shopping WHERE user_id = %s AND week_start > %s", (uid, current_week))
        conn.commit()
        conn.close()

        wtg = weeks_to_goal(p)
        total_phases = wtg["phases"]
        db.set_total_phases(total_phases, user_id=uid)

        days = generate_phase(p, phase_number=1, total_phases=total_phases)
        db.save_training_program(days, user_id=uid, phase_number=1)
        start_date = data.get("start_date") or str(date.today())
        db.set_program_start_date(start_date, user_id=uid)
        return jsonify({"ok": True, "total_phases": total_phases})
    except Exception as e:
        logging.error("[generate_program] %s", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/generate_next_phase", methods=["POST"])
def generate_next_phase():
    """Génère la phase suivante du programme en arrière-plan si besoin."""
    import threading
    from ai import generate_phase

    uid = current_user_id()
    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    prog_info    = db.get_program_info(user_id=uid)
    current_phase = prog_info["phase_number"]
    max_phase     = prog_info["max_phase"]
    total_phases  = prog_info["total_phases"]
    next_phase    = current_phase + 1

    if next_phase > total_phases:
        return jsonify({"ok": True, "started": False, "reason": "Objectif atteint"})
    if max_phase >= next_phase:
        return jsonify({"ok": True, "started": False, "reason": "Déjà générée"})

    # Copie du profil pour le thread
    p_copy = dict(p)

    def _gen():
        try:
            days = generate_phase(p_copy, phase_number=next_phase, total_phases=total_phases)
            db.save_training_program(days, user_id=uid, phase_number=next_phase)
        except Exception as exc:
            print(f"[bg] generate_phase {next_phase} error: {exc}")

    threading.Thread(target=_gen, daemon=True).start()
    return jsonify({"ok": True, "started": True, "next_phase": next_phase, "total_phases": total_phases})


@app.route("/api/week")
def get_week():
    uid = current_user_id()
    week_start = db.get_week_start()
    today_weekday = date.today().weekday()
    days = []

    monday = date.today() - timedelta(days=today_weekday)

    # Déterminer la semaine et la phase courantes du cycle
    prog_info  = db.get_program_info(user_id=uid)
    cur_week   = prog_info["current_week"]
    cur_phase  = prog_info["phase_number"]

    db_program = db.get_training_program(user_id=uid, week_number=cur_week, phase_number=cur_phase)
    has_program = db_program is not None

    if not has_program:
        return jsonify({"days": [], "week_start": week_start, "has_program": False,
                        "prog_info": prog_info})

    for day_num in range(7):
        training_session = db_program.get(day_num, _REPOS_SESSION)
        meal_text = db.get_daily_plan(week_start, day_num, user_id=uid)
        day_date = monday + timedelta(days=day_num)

        days.append({
            "day_num": day_num,
            "day_name": DAYS_FR[day_num],
            "is_today": day_num == today_weekday,
            "is_rest": training_session["name"].upper().startswith("REPOS"),
            "date_str": str(day_date),
            "week_start": week_start,
            "session": {
                "name": training_session["name"],
                "emoji": training_session["emoji"],
                "exercises": training_session["exercises"],
            },
            "meals": meal_text,
        })

    return jsonify({"days": days, "week_start": week_start, "has_program": has_program,
                   "prog_info": prog_info})


@app.route("/api/shopping")
def get_shopping():
    uid = current_user_id()

    def week_label(week_start_str):
        d = date.fromisoformat(week_start_str)
        end = d + timedelta(days=6)
        return f"{d.strftime('%d/%m')} → {end.strftime('%d/%m')}"

    current = db.get_week_start()
    requested = request.args.get("week_start") or current

    # Validation basique du format
    try:
        date.fromisoformat(requested)
    except ValueError:
        requested = current

    return jsonify({
        "week_start": requested,
        "label": week_label(requested),
        "shopping": db.get_weekly_shopping(requested, user_id=uid),
        "is_current": requested == current,
    })


@app.route("/api/shopping/weeks")
def get_shopping_weeks():
    uid = current_user_id()
    conn = db.get_conn()
    c = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c.execute(
        "SELECT week_start FROM weekly_shopping WHERE user_id = %s AND shopping_text IS NOT NULL AND shopping_text != ''",
        (uid,)
    )
    rows = c.fetchall()
    conn.close()
    return jsonify({"weeks": [r["week_start"] for r in rows]})


@app.route("/api/shopping/generate", methods=["POST"])
def generate_shopping():
    from ai import generate_and_store_week_plan
    uid = current_user_id()
    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400
    try:
        next_w = db.get_next_week_start()
        shopping = generate_and_store_week_plan(p, week_start=next_w, user_id=uid)
        return jsonify({"ok": True, "shopping": shopping})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/month")
def get_month():
    from flask import request as freq
    today = date.today()
    year = int(freq.args.get("year", today.year))
    month = int(freq.args.get("month", today.month))
    uid = current_user_id()

    # Premier et dernier jour du mois
    first_day = date(year, month, 1)
    last_day = date(year, month, calendar.monthrange(year, month)[1])

    conn = db.get_conn()
    c_meal = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    c_meal.execute("SELECT week_start, day_of_week FROM daily_plan WHERE user_id = %s", (uid,))
    meal_rows = c_meal.fetchall()
    conn.close()

    # Index : (week_start, day_of_week) -> True
    meals_index = set()
    for row in meal_rows:
        meals_index.add((row["week_start"], row["day_of_week"]))

    days = []
    current = first_day
    prog_info   = db.get_program_info(user_id=uid)
    base_start  = prog_info.get("program_start_date")
    total_weeks = prog_info.get("total_weeks", 4)

    # Pré-charger tous les programmes disponibles (phases × semaines) en une seule requête
    _conn = db.get_conn()
    _cur = _conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    _cur.execute(
        "SELECT phase_number, week_number, day_of_week, session_name, session_emoji, exercises_json "
        "FROM training_program WHERE user_id = %s", (uid,)
    )
    _rows = _cur.fetchall()
    _conn.close()
    all_programs: dict = {}
    for _r in _rows:
        _p, _w = _r["phase_number"], _r["week_number"]
        all_programs.setdefault(_p, {}).setdefault(_w, {})[_r["day_of_week"]] = {
            "name": _r["session_name"],
            "emoji": _r["session_emoji"],
            "exercises": json.loads(_r["exercises_json"]),
        }
    max_avail_phase = max(all_programs.keys()) if all_programs else 1

    def _get_session(phase: int, week: int, weekday: int) -> dict:
        """Récupère une session depuis le cache, avec fallback REPOS."""
        p_key = phase if phase in all_programs else max_avail_phase
        w_key = week if week in all_programs.get(p_key, {}) else 1
        return all_programs.get(p_key, {}).get(w_key, {}).get(weekday, _REPOS_SESSION)

    while current <= last_day:
        weekday = current.weekday()
        if base_start:
            from datetime import date as _date
            start_d = _date.fromisoformat(base_start)
            w_elapsed  = max(0, (current - start_d).days // 7)
            day_week   = (w_elapsed % total_weeks) + 1
            day_phase  = (w_elapsed // total_weeks) + 1
        else:
            day_week  = 1
            day_phase = 1
        training_session = _get_session(day_phase, day_week, weekday)
        week_start = db.get_week_start(current)
        has_meals = (week_start, weekday) in meals_index

        days.append({
            "date": str(current),
            "day_num": current.day,
            "weekday": weekday,
            "is_today": current == today,
            "is_rest": training_session["name"].upper().startswith("REPOS"),
            "session_emoji": training_session["emoji"],
            "session_name": training_session["name"],
            "exercises": training_session["exercises"],
            "has_meals": has_meals,
            "week_start": week_start,
        })
        current += timedelta(days=1)

    month_name = first_day.strftime("%B %Y")
    # Traduire le nom du mois
    months_fr = {
        "January": "Janvier", "February": "Février", "March": "Mars",
        "April": "Avril", "May": "Mai", "June": "Juin",
        "July": "Juillet", "August": "Août", "September": "Septembre",
        "October": "Octobre", "November": "Novembre", "December": "Décembre"
    }
    for en, fr in months_fr.items():
        month_name = month_name.replace(en, fr)

    return jsonify({
        "days": days,
        "month_name": month_name,
        "year": year,
        "month": month,
        "first_weekday": first_day.weekday(),  # 0=lundi
    })


@app.route("/api/regenerate_day", methods=["POST"])
def regenerate_day():
    from ai import generate_daily_meals
    from training import is_rest_day

    uid = current_user_id()
    data = request.get_json()
    day_date_str = data.get("date")
    if not day_date_str:
        return jsonify({"ok": False, "error": "date manquante"}), 400

    try:
        day_date = date.fromisoformat(day_date_str)
    except ValueError:
        return jsonify({"ok": False, "error": "date invalide"}), 400

    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "profil manquant"}), 400

    weekday = day_date.weekday()
    rest_day = is_rest_day(weekday)
    targets = prof.get_daily_targets(p, is_rest_day=rest_day)
    day_name = DAYS_FR[weekday]

    meal_text = generate_daily_meals(targets, day_name)
    week_start = db.get_week_start(day_date)
    db.save_daily_plan(week_start, weekday, meal_text, user_id=uid)

    return jsonify({"ok": True, "meals": meal_text})


@app.route("/api/regenerate_meal", methods=["POST"])
def regenerate_meal():
    from ai import regenerate_single_meal
    from training import is_rest_day
    import re

    uid = current_user_id()
    data = request.get_json()
    day_date_str = data.get("date")
    meal_type = data.get("meal_type")

    if not day_date_str or not meal_type:
        return jsonify({"ok": False, "error": "date ou meal_type manquant"}), 400

    valid_types = ["petit_dejeuner", "dejeuner", "collation", "diner"]
    if meal_type not in valid_types:
        return jsonify({"ok": False, "error": "meal_type invalide"}), 400

    try:
        day_date = date.fromisoformat(day_date_str)
    except ValueError:
        return jsonify({"ok": False, "error": "date invalide"}), 400

    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "profil manquant"}), 400

    weekday = day_date.weekday()
    rest_day = is_rest_day(weekday)
    total_targets = prof.get_daily_targets(p, is_rest_day=rest_day)
    day_name = DAYS_FR[weekday]

    week_start = db.get_week_start(day_date)
    current_text = db.get_daily_plan(week_start, weekday, user_id=uid) or ""

    # Calcule les macros déjà consommées par les AUTRES repas
    meal_emojis = {
        "petit_dejeuner": "🌅", "dejeuner": "🍽",
        "collation": "🍎",      "diner": "🌙"
    }
    used = {"calories": 0, "protein_g": 0, "carbs_g": 0, "fat_g": 0}

    for mtype, emoji in meal_emojis.items():
        if mtype == meal_type:
            continue
        # Cherche le bloc de ce repas dans le texte
        pattern = re.compile(
            rf'\{emoji}[^\n]*\((\d+)kcal\s*\|\s*(\d+)g\s*P\s*\|\s*(\d+)g\s*G\s*\|\s*(\d+)g\s*L\)',
            re.IGNORECASE
        )
        m = pattern.search(current_text)
        if m:
            used["calories"]  += int(m.group(1))
            used["protein_g"] += int(m.group(2))
            used["carbs_g"]   += int(m.group(3))
            used["fat_g"]     += int(m.group(4))
        else:
            pass

    # Budget restant = total - autres repas
    remaining = {
        "calories":  max(50,  total_targets["calories"]  - used["calories"]),
        "protein_g": max(10,  total_targets["protein_g"] - used["protein_g"]),
        "carbs_g":   max(5,   total_targets["carbs_g"]   - used["carbs_g"]),
        "fat_g":     max(5,   total_targets["fat_g"]     - used["fat_g"]),
    }

    new_meal = regenerate_single_meal(meal_type, day_name, remaining, current_text)

    updated_text = _replace_meal_section(current_text, meal_type, new_meal)
    updated_text = _recalc_total(updated_text)
    db.save_daily_plan(week_start, weekday, updated_text, user_id=uid)

    return jsonify({"ok": True, "meals": updated_text, "new_meal": new_meal})


def _replace_meal_section(full_text: str, meal_type: str, new_meal: str) -> str:
    """Remplace une section repas dans le texte complet du jour."""
    markers = {
        "petit_dejeuner": "🌅",
        "dejeuner":       "🍽",
        "collation":      "🍎",
        "diner":          "🌙",
    }
    # Ordre des sections pour délimiter chaque bloc
    order = ["🌅", "🍽", "🍎", "🌙"]
    target_emoji = markers.get(meal_type)
    if not target_emoji or target_emoji not in full_text:
        # Section absente, on ajoute à la fin
        return full_text.rstrip() + "\n\n" + new_meal

    idx = order.index(target_emoji)
    next_emojis = order[idx + 1:]

    lines = full_text.split("\n")
    in_section = False
    start_line = end_line = -1

    for i, line in enumerate(lines):
        if target_emoji in line:
            in_section = True
            start_line = i
        elif in_section and any(e in line for e in next_emojis):
            end_line = i
            break

    if start_line == -1:
        return full_text.rstrip() + "\n\n" + new_meal

    if end_line == -1:
        end_line = len(lines)

    new_lines = lines[:start_line] + new_meal.split("\n") + [""] + lines[end_line:]
    return "\n".join(new_lines)


def _recalc_total(text: str) -> str:
    """Recalcule la ligne 'Total estimé' en sommant les macros de tous les repas du texte."""
    import re
    pattern = re.compile(r'\((\d+)kcal\s*\|\s*(\d+)g\s*P\s*\|\s*(\d+)g\s*G\s*\|\s*(\d+)g\s*L\)', re.IGNORECASE)
    cal = prot = carb = fat = 0
    for m in pattern.finditer(text):
        cal  += int(m.group(1))
        prot += int(m.group(2))
        carb += int(m.group(3))
        fat  += int(m.group(4))
    new_total = f"Total estimé : {cal}kcal | {prot}g P | {carb}g G | {fat}g L"
    # Remplace la ligne existante ou l'ajoute à la fin
    updated = re.sub(r"Total estimé\s*:.*", new_total, text)
    if updated == text:
        updated = text.rstrip() + "\n\n" + new_total
    return updated


@app.route("/api/day_meals")
def get_day_meals():
    from flask import request as freq
    uid = current_user_id()
    day_date = freq.args.get("date")
    if not day_date:
        return jsonify({"meals": None})
    try:
        d = date.fromisoformat(day_date)
    except ValueError:
        return jsonify({"meals": None})
    week_start = db.get_week_start(d)
    meals = db.get_daily_plan(week_start, d.weekday(), user_id=uid)
    return jsonify({"meals": meals})


@app.route("/api/week_meal_texts")
def get_week_meal_texts():
    """Retourne les 7 textes de repas d'une semaine donnée (pour le wizard)."""
    uid = current_user_id()
    week_start = request.args.get("week_start")
    if not week_start:
        return jsonify({"texts": [""] * 7})
    texts = [db.get_daily_plan(week_start, d, user_id=uid) or "" for d in range(7)]
    return jsonify({"texts": texts})


@app.route("/api/suggest_day_meals", methods=["POST"])
def suggest_day_meals():
    """Génère une suggestion de repas pour un jour donné (utilisé par le wizard)."""
    from ai import generate_daily_meals
    from training import is_rest_day

    uid = current_user_id()
    data = request.get_json()
    day_num = data.get("day")
    if day_num is None or not (0 <= day_num <= 6):
        return jsonify({"ok": False, "error": "day invalide"}), 400

    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    rest_day = is_rest_day(day_num)
    targets = prof.get_daily_targets(p, is_rest_day=rest_day)
    day_name = DAYS_FR[day_num]

    try:
        meals = generate_daily_meals(targets, day_name)
        return jsonify({"ok": True, "meals": meals})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/suggest_week_meals", methods=["POST"])
def suggest_week_meals():
    """Génère les repas des 7 jours en un seul appel IA. Retourne la liste des textes."""
    from ai import generate_and_store_week_plan

    uid = current_user_id()
    data = request.get_json()
    week_start = data.get("week_start")

    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    try:
        if week_start:
            date.fromisoformat(week_start)  # validation
        else:
            week_start = db.get_next_week_start()

        _generate_week_meals_only(p, week_start, user_id=uid)
        texts = [db.get_daily_plan(week_start, d, user_id=uid) or "" for d in range(7)]
        return jsonify({"ok": True, "texts": texts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _generate_week_meals_only(profile: dict, week_start: str, user_id: int = 1, from_day: int = 0):
    """Appel IA unique : génère et sauvegarde les repas sans toucher aux courses.
    from_day : 0=Lundi … 6=Dimanche — seuls les jours >= from_day sont générés.
    """
    from ai import _call, HAIKU
    from profile import get_daily_targets

    targets_train = get_daily_targets(profile, is_rest_day=False)
    targets_rest  = get_daily_targets(profile, is_rest_day=True)

    days_needed = list(range(from_day, 7))  # ex. [3,4,5,6] si démarrage jeudi
    days_label  = ", ".join(str(d) for d in days_needed)

    prompt = f"""Génère un plan repas varié pour les jours {days_label} (0=Lundi … 6=Dimanche).
Personne : {profile.get('weight_kg')} kg, objectif recomposition corporelle.
Macros cibles :
Jours entraînement : {targets_train['calories']} kcal | {targets_train['protein_g']}g P | {targets_train['carbs_g']}g G | {targets_train['fat_g']}g L
Jour repos : {targets_rest['calories']} kcal | {targets_rest['protein_g']}g P | {targets_rest['carbs_g']}g G | {targets_rest['fat_g']}g L
Cuisine française/méditerranéenne. Variété de protéines. Évite de répéter le même plat plusieurs jours.

Réponds UNIQUEMENT avec du JSON valide, tableau de {len(days_needed)} objet(s) :
[
  {{"day": {days_needed[0]}, "text": "🌅 Petit-déjeuner (Xkcal | Xg P | Xg G | Xg L)\\n[repas]\\n\\n🍽 Déjeuner (Xkcal | Xg P | Xg G | Xg L)\\n[repas]\\n\\n🍎 Collation 17h30 (Xkcal | Xg P | Xg G | Xg L)\\n[collation]\\n\\n🌙 Dîner (Xkcal | Xg P | Xg G | Xg L)\\n[repas]\\n\\nTotal estimé : Xkcal | Xg P | Xg G | Xg L"}},
  ...
]"""

    raw = _call(prompt, model=HAIKU, max_tokens=5000)
    try:
        s = raw.find("[")
        e = raw.rfind("]") + 1
        entries = json.loads(raw[s:e])
    except Exception:
        entries = []

    for entry in entries:
        day = entry.get("day")
        text = entry.get("text", "")
        if day is not None and text and int(day) >= from_day:
            db.save_daily_plan(week_start, int(day), text, user_id=user_id)


@app.route("/api/generate_initial_meals", methods=["POST"])
def generate_initial_meals():
    """Génère les repas pour les jours restants de la semaine à partir d'une date choisie."""
    from datetime import date as _date

    uid = current_user_id()
    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    data = request.get_json() or {}
    start_date_str = data.get("start_date")
    try:
        start_date = _date.fromisoformat(start_date_str) if start_date_str else _date.today()
    except ValueError:
        start_date = _date.today()

    week_start = db.get_week_start(start_date)
    from_day   = start_date.weekday()  # 0=Lun … 6=Dim

    p["extra_sports"] = parse_extra_sports(p.get("extra_sports"))

    try:
        _generate_week_meals_only(p, week_start, user_id=uid, from_day=from_day)
        return jsonify({"ok": True, "week_start": week_start, "from_day": from_day})
    except Exception as e:
        logging.error("[generate_initial_meals] %s", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/shopping/generate_from_plan", methods=["POST"])
def generate_shopping_from_plan():
    """Reçoit les repas choisis pour la semaine, les sauvegarde et génère la liste de courses."""
    from ai import generate_shopping_from_meals

    uid = current_user_id()
    data = request.get_json()
    week_start = data.get("week_start")
    days = data.get("days", [])

    if not week_start or not days:
        return jsonify({"ok": False, "error": "Données manquantes"}), 400

    # Validation basique du week_start (format YYYY-MM-DD)
    try:
        date.fromisoformat(week_start)
    except ValueError:
        return jsonify({"ok": False, "error": "week_start invalide"}), 400

    # Sauvegarder les plans de repas
    meals_summary = ""
    for entry in days:
        day_num = entry.get("day")
        text = str(entry.get("text", "")).strip()
        if day_num is None or not (0 <= int(day_num) <= 6):
            continue
        if text:
            db.save_daily_plan(week_start, int(day_num), text, user_id=uid)
            meals_summary += f"{DAYS_FR[int(day_num)]} :\n{text[:400]}\n\n"

    if not meals_summary:
        return jsonify({"ok": False, "error": "Aucun repas renseigné"}), 400

    try:
        shopping = generate_shopping_from_meals(meals_summary)
        db.save_weekly_shopping(week_start, shopping, user_id=uid)
        return jsonify({"ok": True, "shopping": shopping})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/log_weight", methods=["POST"])
def log_weight_route():
    uid = current_user_id()
    data = request.get_json()
    try:
        weight = float(data.get("weight_kg", 0))
    except (TypeError, ValueError):
        return jsonify({"ok": False, "error": "Poids invalide"}), 400

    if not (20 <= weight <= 300):
        return jsonify({"ok": False, "error": "Poids hors limites"}), 400

    db.log_weight(weight, user_id=uid)
    logs = db.get_weight_logs(limit=7, user_id=uid)
    return jsonify({"ok": True, "logs": [{"weight_kg": r["weight_kg"], "logged_at": r["logged_at"]} for r in logs]})


if __name__ == "__main__":
    db.init_db()
    port = int(os.getenv("WEB_PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
