"""
Interface web Flask — application de coaching sportif.
"""
import os
import traceback
import logging
import urllib.parse
from flask import Flask, render_template, request, jsonify, session, redirect, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

import json
import psycopg2.extras
import db
import ai as ai_mod
import profile as prof
from profile import parse_extra_sports, parse_rest_days
from training import DAYS_FR


def _is_rest_day(profile: dict, weekday: int) -> bool:
    return weekday in set(parse_rest_days(profile.get("rest_days", [])))


def _get_targets(profile: dict, weekday: int) -> dict:
    """Lit les besoins nutritionnels stockés en DB — calculés une fois à la sauvegarde du profil."""
    is_rest = _is_rest_day(profile, weekday)
    key = "macros_rest" if is_rest else "macros_training"
    raw = profile.get(key)
    if raw:
        try:
            return json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            pass
    # Fallback si pas encore calculé (ancien profil)
    return prof.get_daily_targets(profile, is_rest_day=is_rest)

_REPOS_SESSION = {"name": "REPOS", "emoji": "😴", "exercises": []}
from datetime import date, timedelta
import calendar

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")

# Initialise les tables au démarrage (gunicorn ou direct)
db.init_db()


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
    """Retourne l'ID du profil actif (principal ou partenaire en mode couple)."""
    return session.get("active_uid", session["user_id"])


def _load_couple_into_session(user_id: int):
    """Charge les infos partenaire dans la session si l'utilisateur est en couple."""
    partner_id = db.get_partner_user_id(user_id)
    if partner_id:
        p = db.get_profile(user_id=partner_id)
        session["partner_user_id"] = partner_id
        session["partner_name"] = p["name"] if p else "Partenaire"
    else:
        session.pop("partner_user_id", None)
        session.pop("partner_name", None)


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
    _load_couple_into_session(user_id)
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
    _load_couple_into_session(user["id"])
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
    return jsonify({"user": {
        "id": uid,
        "username": username,
        "partner_user_id": session.get("partner_user_id"),
        "partner_name": session.get("partner_name"),
        "viewing_partner": session.get("active_uid") == session.get("partner_user_id"),
    }})


# ── Mode Couple ────────────────────────────────────────────────────────────────

@app.route("/api/couple/status")
def couple_status():
    uid = session["user_id"]
    # Toujours vérifier la DB (la session peut être obsolète)
    partner_id = db.get_partner_user_id(uid)
    if partner_id and not session.get("partner_user_id"):
        # Resynchronise la session si le lien existe en DB mais pas en session
        _load_couple_into_session(uid)
    partner_id = session.get("partner_user_id")
    viewing_partner = session.get("active_uid") == partner_id if partner_id else False
    return jsonify({
        "is_couple": bool(partner_id),
        "partner_user_id": partner_id,
        "partner_name": session.get("partner_name"),
        "viewing_partner": viewing_partner,
        "active_uid": current_user_id(),
    })


@app.route("/api/couple/switch", methods=["POST"])
def couple_switch():
    """Bascule entre le profil principal et le profil partenaire."""
    uid = session["user_id"]
    partner_id = session.get("partner_user_id")
    if not partner_id:
        return jsonify({"ok": False, "error": "Pas en mode couple"}), 400

    data = request.get_json() or {}
    target = data.get("target")  # "main" ou "partner"

    if target == "partner":
        session["active_uid"] = partner_id
    else:
        session.pop("active_uid", None)

    viewing_partner = session.get("active_uid") == partner_id
    return jsonify({"ok": True, "viewing_partner": viewing_partner, "active_uid": current_user_id()})


@app.route("/api/couple/setup", methods=["POST"])
def couple_setup():
    """Crée le profil partenaire, génère son programme, lie les deux utilisateurs."""
    from ai import generate_phase
    from profile import weeks_to_goal
    import random, string

    uid = session["user_id"]
    main_username = session["username"]
    data = request.get_json() or {}

    required = ["name", "age", "weight_kg", "height_cm", "goal_weight_kg"]
    if not all(k in data for k in required):
        return jsonify({"ok": False, "error": "Champs partenaire manquants"}), 400

    try:
        # Crée un compte partenaire (username auto, mot de passe aléatoire)
        partner_username = f"{main_username}_p"
        existing = db.get_user_by_username(partner_username)
        if existing:
            partner_id = existing["id"]
        else:
            rand_pw = ''.join(random.choices(string.ascii_letters + string.digits, k=20))
            partner_id = db.create_user(partner_username, generate_password_hash(rand_pw))

        # Profil partenaire
        main_profile = db.get_profile(user_id=uid)
        partner_payload = {
            "name":                  str(data["name"]).strip(),
            "age":                   int(data["age"]),
            "weight_kg":             float(data["weight_kg"]),
            "height_cm":             int(data["height_cm"]),
            "goal_weight_kg":        float(data["goal_weight_kg"]),
            "goal":                  data.get("goal", "recomposition"),
            "sexe":                  data.get("sexe", "femme"),
            "job_type":              data.get("job_type", main_profile.get("job_type", "bureau")) if main_profile else "bureau",
            "gym_sessions_per_week": int(data.get("gym_sessions_per_week", main_profile.get("gym_sessions_per_week", 3))) if main_profile else 3,
            "fitness_level":         data.get("fitness_level", "intermediaire"),
            "activity_level":        "modere",
            "training_days":         "0,1,2,3,5,6",
            "extra_sports":          data.get("extra_sports", []),
            "rest_days":             data.get("rest_days", []),
        }
        db.save_profile(partner_payload, user_id=partner_id)
        db.log_weight(partner_payload["weight_kg"], user_id=partner_id)

        # Lien couple en premier — comme ça le switcher fonctionne même si la suite plante
        db.create_couple(uid, partner_id)
        session["partner_user_id"] = partner_id
        session["partner_name"] = partner_payload["name"]

        # Programme d'entraînement partenaire
        p_partner = db.get_profile(user_id=partner_id)
        wtg = weeks_to_goal(p_partner)
        total_phases = wtg["phases"]
        db.set_total_phases(total_phases, user_id=partner_id)

        days = generate_phase(p_partner, phase_number=1, total_phases=total_phases)
        db.save_training_program(days, user_id=partner_id, phase_number=1)

        start_date_str = data.get("start_date") or str(date.today())
        db.set_program_start_date(start_date_str, user_id=partner_id)

        # Repas partenaire (mêmes plats, macros adaptées)
        p_main = db.get_profile(user_id=uid)
        week_start = db.get_week_start()
        if p_main:
            p_partner["extra_sports"] = parse_extra_sports(p_partner.get("extra_sports"))
            _apply_partner_macros_to_existing_meals(p_partner, week_start, partner_id, uid)

        return jsonify({"ok": True, "partner_id": partner_id, "partner_name": partner_payload["name"]})
    except Exception as e:
        logging.error("[couple_setup] %s", traceback.format_exc())
        return jsonify({"ok": False, "error": str(e)}), 500


def _apply_partner_macros_to_existing_meals(partner_profile: dict, week_start: str,
                                             partner_user_id: int, main_user_id: int):
    """Copie les repas du profil principal en adaptant les macros au profil partenaire."""
    targets_train = prof.get_daily_targets(partner_profile, is_rest_day=False)
    targets_rest  = prof.get_daily_targets(partner_profile, is_rest_day=True)
    rest_days_set = set(parse_rest_days(partner_profile.get("rest_days", [])))

    # Ratio calorique partenaire / profil principal (jour d'entraînement)
    main_profile    = db.get_profile(user_id=main_user_id)
    main_cals       = prof.get_daily_targets(main_profile, is_rest_day=False).get("calories", 2000)
    partner_cals    = targets_train.get("calories", main_cals)
    ratio           = partner_cals / main_cals if main_cals else 1.0

    for day in range(7):
        main_text = db.get_daily_plan(week_start, day, user_id=main_user_id)
        if not main_text:
            continue

        is_rest = day in rest_days_set
        targets = targets_rest if is_rest else targets_train
        macros  = ai_mod._split_macros(targets)
        descs   = _extract_meal_descriptions(main_text)
        scaled  = {k: _scale_quantities(v, ratio) for k, v in descs.items()}
        text    = ai_mod._build_meal_text(scaled, macros, targets)
        db.save_daily_plan(week_start, day, text, user_id=partner_user_id)


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
    macros = _get_targets(p, weekday)

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

        # Mise à jour des repas de la semaine avec les nouvelles macros
        try:
            week_start = db.get_week_start()
            p_updated = db.get_profile(user_id=uid)
            p_updated["extra_sports"] = parse_extra_sports(p_updated.get("extra_sports"))
            rest_days_set = set(parse_rest_days(p_updated.get("rest_days", [])))

            # Rescale les repas du profil modifié (nouvelles macros, mêmes descriptions)
            for day in range(7):
                text = db.get_daily_plan(week_start, day, user_id=uid)
                if not text:
                    continue
                is_rest = day in rest_days_set
                targets = prof.get_daily_targets(p_updated, is_rest_day=is_rest)
                macros  = ai_mod._split_macros(targets)
                descs   = _extract_meal_descriptions(text)
                new_text = ai_mod._build_meal_text(descs, macros, targets)
                db.save_daily_plan(week_start, day, new_text, user_id=uid)

            # Si couple et que l'utilisateur actif est le profil PRINCIPAL : rescale le partenaire
            main_uid    = session["user_id"]
            partner_uid = db.get_partner_user_id(main_uid)
            if partner_uid and partner_uid != uid:
                p_partner = db.get_profile(user_id=partner_uid)
                if p_partner:
                    p_partner["extra_sports"] = parse_extra_sports(p_partner.get("extra_sports"))
                    _apply_partner_macros_to_existing_meals(p_partner, week_start, partner_uid, main_uid)
        except Exception:
            logging.warning("[save_profile] meal rescale failed (non-fatal): %s", traceback.format_exc())

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

    # Récupère les perfs de la phase courante pour adapter la prochaine
    prog_info = db.get_program_info(user_id=uid)
    start_str = prog_info.get("program_start_date")
    total_weeks = prog_info.get("total_weeks", 4)
    perf_summary = []
    if start_str:
        from datetime import date as _date, timedelta as _td
        start_d = _date.fromisoformat(str(start_str))
        phase_start = start_d + _td(weeks=(current_phase - 1) * total_weeks)
        phase_end   = phase_start + _td(weeks=total_weeks)
        perf_summary = db.get_phase_exercise_summary(
            str(phase_start), str(phase_end), user_id=uid
        )

    # Copie du profil pour le thread
    p_copy = dict(p)

    def _gen():
        try:
            days = generate_phase(p_copy, phase_number=next_phase,
                                  total_phases=total_phases, perf_summary=perf_summary)
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
        profile = db.get_profile(user_id=uid)
        has_profile = profile is not None and profile.get("name")
        return jsonify({"days": [], "week_start": week_start, "has_program": False,
                        "has_profile": bool(has_profile), "prog_info": prog_info})

    done_dates = db.get_done_dates(week_start, user_id=uid)

    for day_num in range(7):
        training_session = db_program.get(day_num, _REPOS_SESSION)
        meal_text = db.get_daily_plan(week_start, day_num, user_id=uid)
        day_date = monday + timedelta(days=day_num)

        # Appliquer les overrides d'exercices pour ce jour
        overrides = db.get_exercise_overrides(str(day_date), user_id=uid)
        exercises = []
        for ex in training_session["exercises"]:
            ov = overrides.get(ex["name"])
            if ov:
                exercises.append({
                    "name":     ov["new_name"],
                    "sets":     ov["new_sets"] or ex.get("sets", ""),
                    "rest":     ov["new_rest"] or ex.get("rest", ""),
                    "replaced": True,
                    "original": ex["name"],
                })
            else:
                exercises.append(ex)

        days.append({
            "day_num": day_num,
            "day_name": DAYS_FR[day_num],
            "is_today": day_num == today_weekday,
            "is_rest": training_session["name"].upper().startswith("REPOS"),
            "date_str": str(day_date),
            "week_start": week_start,
            "is_done": str(day_date) in done_dates,
            "session": {
                "name": training_session["name"],
                "emoji": training_session["emoji"],
                "exercises": exercises,
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


@app.route("/api/replace_exercise", methods=["POST"])
def replace_exercise():
    uid = current_user_id()
    data = request.get_json() or {}
    log_date      = data.get("date", str(date.today()))
    original_name = str(data.get("original_name", "")).strip()
    new_name      = str(data.get("new_name", "")).strip()
    new_sets      = str(data.get("new_sets", "")).strip() or None
    new_rest      = str(data.get("new_rest", "")).strip() or None
    if not original_name or not new_name:
        return jsonify({"ok": False, "error": "Champs manquants"}), 400
    db.save_exercise_override(log_date, original_name, new_name, new_sets, new_rest, user_id=uid)
    return jsonify({"ok": True})


@app.route("/api/session_done", methods=["POST"])
def toggle_session_done():
    uid = current_user_id()
    data = request.get_json() or {}
    log_date = data.get("date", str(date.today()))
    try:
        date.fromisoformat(log_date)
    except ValueError:
        return jsonify({"ok": False, "error": "date invalide"}), 400
    done = db.toggle_session_done(log_date, user_id=uid)
    return jsonify({"ok": True, "done": done})


@app.route("/api/substitute_exercise", methods=["POST"])
def substitute_exercise():
    from ai import generate_exercise_substitutes
    data = request.get_json() or {}
    exercise_name = str(data.get("exercise_name", "")).strip()
    session_name  = str(data.get("session_name", "")).strip()
    if not exercise_name:
        return jsonify({"ok": False, "error": "exercise_name manquant"}), 400
    try:
        alts = generate_exercise_substitutes(exercise_name, session_name)
        return jsonify({"ok": True, "alternatives": alts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/weekly_report")
def get_weekly_report():
    from ai import generate_weekly_report
    uid = current_user_id()
    week_start = request.args.get("week_start") or db.get_week_start()

    cached = db.get_weekly_report(week_start, user_id=uid)
    if cached:
        return jsonify({"ok": True, "report": cached, "cached": True})

    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    # Calcul des stats de la semaine
    done_dates   = db.get_done_dates(week_start, user_id=uid)
    prog_info    = db.get_program_info(user_id=uid)
    cur_week     = prog_info["current_week"]
    cur_phase    = prog_info["phase_number"]
    db_program   = db.get_training_program(user_id=uid, week_number=cur_week, phase_number=cur_phase) or {}
    sessions_total = sum(
        1 for d in db_program.values()
        if not d["name"].upper().startswith("REPOS") and d.get("exercises")
    )

    weight_logs  = db.get_weight_logs(limit=14, user_id=uid)
    weight_change = None
    current_weight = None
    if len(weight_logs) >= 2:
        current_weight = weight_logs[0]["weight_kg"]
        week_ago = weight_logs[-1]["weight_kg"]
        weight_change = round(current_weight - week_ago, 1)
    elif weight_logs:
        current_weight = weight_logs[0]["weight_kg"]

    meals_planned = sum(
        1 for d in range(7)
        if db.get_daily_plan(week_start, d, user_id=uid)
    )

    stats = {
        "sessions_done":   len(done_dates),
        "sessions_total":  sessions_total,
        "weight_change":   weight_change,
        "current_weight":  current_weight,
        "meals_planned":   meals_planned,
    }

    try:
        report = generate_weekly_report(p, stats)
        db.save_weekly_report(week_start, report, user_id=uid)
        return jsonify({"ok": True, "report": report, "cached": False})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


_exercise_image_cache: dict = {}

@app.route("/api/exercise_image")
def get_exercise_image():
    import urllib.request as _urllib
    from ai import exercise_name_to_english

    name = request.args.get("name", "").strip()
    if not name:
        return jsonify({"ok": False, "images": []})

    if name in _exercise_image_cache:
        return jsonify({"ok": True, "images": _exercise_image_cache[name]})

    try:
        en_name = exercise_name_to_english(name)
        search_url = f"https://wger.de/api/v2/exercise/search/?term={urllib.parse.quote(en_name)}&language=english&format=json"
        with _urllib.urlopen(search_url, timeout=5) as r:
            suggestions = json.loads(r.read()).get("suggestions", [])

        images = []
        for s in suggestions[:5]:
            base_id = s["data"]["base_id"]
            info_url = f"https://wger.de/api/v2/exerciseinfo/{base_id}/?format=json"
            with _urllib.urlopen(info_url, timeout=5) as r:
                info = json.loads(r.read())
            imgs = [i["image"] for i in info.get("images", []) if i.get("image")]
            if imgs:
                images = imgs[:2]
                break

        _exercise_image_cache[name] = images
        return jsonify({"ok": True, "images": images, "searched": en_name})
    except Exception as e:
        logging.warning("[exercise_image] %s", e)
        return jsonify({"ok": False, "images": []})


@app.route("/api/recipe", methods=["POST"])
def get_recipe():
    from ai import generate_recipe
    data = request.get_json() or {}
    description = str(data.get("description", "")).strip()
    if not description:
        return jsonify({"ok": False, "error": "description manquante"}), 400
    try:
        recipe = generate_recipe(description)
        return jsonify({"ok": True, "recipe": recipe})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/log_session", methods=["GET"])
def get_session_log():
    uid = current_user_id()
    log_date = request.args.get("date", str(date.today()))
    logs = db.get_exercise_logs(log_date, user_id=uid)
    return jsonify({"logs": logs})


@app.route("/api/log_session", methods=["POST"])
def post_session_log():
    uid = current_user_id()
    data = request.get_json() or {}
    log_date = data.get("date", str(date.today()))
    exercises = data.get("exercises", [])

    try:
        date.fromisoformat(log_date)
    except ValueError:
        return jsonify({"ok": False, "error": "date invalide"}), 400

    clean = []
    for ex in exercises:
        name = str(ex.get("exercise_name", "")).strip()
        if not name:
            continue
        weight = ex.get("weight_kg")
        clean.append({
            "exercise_name": name,
            "sets_done": int(ex["sets_done"]) if ex.get("sets_done") else None,
            "reps": str(ex["reps"]).strip() if ex.get("reps") else None,
            "weight_kg": float(weight) if weight not in (None, "", 0) else None,
        })

    db.save_exercise_logs(log_date, clean, user_id=uid)
    return jsonify({"ok": True})


@app.route("/api/regenerate_day", methods=["POST"])
def regenerate_day():
    from ai import _call, HAIKU, _split_macros, _build_meal_text
    import json as _json

    uid = current_user_id()
    main_uid = session["user_id"]
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
    day_name = DAYS_FR[weekday]
    week_start = db.get_week_start(day_date)

    # Génère les descriptions de plats avec quantités
    prompt = f"""Propose 4 descriptions de repas pour {day_name}.
Cuisine variée et moderne : méditerranéen, asiatique, mexicain, libanais, américain healthy, français revisité.
Noms de plats concrets et appétissants avec ingrédients principaux et quantités en grammes (ex: "Saumon grillé 150g + riz basmati 80g + courgettes sautées 120g").

Réponds UNIQUEMENT en JSON :
{{"petit_dejeuner": "...", "dejeuner": "...", "collation": "...", "diner": "..."}}"""

    raw = _call(prompt, model=HAIKU, max_tokens=400)
    try:
        s, e = raw.find("{"), raw.rfind("}") + 1
        descriptions = _json.loads(raw[s:e])
    except Exception:
        descriptions = {
            "petit_dejeuner": "Œufs brouillés + pain complet + fromage blanc",
            "dejeuner": "Poulet grillé + riz complet + légumes vapeur",
            "collation": "Yaourt grec + fruits rouges + amandes",
            "diner": "Saumon + patates douces + brocoli",
        }

    # Construit et sauvegarde pour le profil actif
    targets = _get_targets(p, weekday)
    macros = _split_macros(targets)
    meal_text = _build_meal_text(descriptions, macros, targets)
    db.save_daily_plan(week_start, weekday, meal_text, user_id=uid)

    # En mode couple : mêmes plats, quantités et macros adaptées pour le partenaire
    partner_uid = db.get_partner_user_id(main_uid)
    if partner_uid and partner_uid != uid:
        p_partner = db.get_profile(user_id=partner_uid)
        if p_partner:
            p_targets = _get_targets(p_partner, weekday)
            p_macros = _split_macros(p_targets)
            ratio = p_targets['calories'] / targets['calories'] if targets.get('calories') else 1.0
            scaled = {k: _scale_quantities(v, ratio) if isinstance(v, str) else v
                      for k, v in descriptions.items()}
            p_text = _build_meal_text(scaled, p_macros, p_targets)
            db.save_daily_plan(week_start, weekday, p_text, user_id=partner_uid)

    return jsonify({"ok": True, "meals": meal_text})


@app.route("/api/regenerate_meal", methods=["POST"])
def regenerate_meal():
    from ai import regenerate_single_meal
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
    total_targets = _get_targets(p, weekday)
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
        # Regex permissif : espace optionnel autour de kcal, virgule ou point décimal
        pattern = re.compile(
            rf'{re.escape(emoji)}[^\n]*\(\s*(\d+)\s*kcal\s*\|\s*(\d+(?:[.,]\d+)?)\s*g\s*P\s*\|\s*(\d+(?:[.,]\d+)?)\s*g\s*G\s*\|\s*(\d+(?:[.,]\d+)?)\s*g\s*L\s*\)',
            re.IGNORECASE
        )
        m = pattern.search(current_text)
        if m:
            used["calories"]  += int(m.group(1))
            used["protein_g"] += int(float(m.group(2).replace(",", ".")))
            used["carbs_g"]   += int(float(m.group(3).replace(",", ".")))
            used["fat_g"]     += int(float(m.group(4).replace(",", ".")))

    # Budget restant = total - autres repas
    remaining = {
        "calories":  max(50,  total_targets["calories"]  - used["calories"]),
        "protein_g": max(10,  total_targets["protein_g"] - used["protein_g"]),
        "carbs_g":   max(5,   total_targets["carbs_g"]   - used["carbs_g"]),
        "fat_g":     max(5,   total_targets["fat_g"]     - used["fat_g"]),
    }

    new_meal = regenerate_single_meal(meal_type, day_name, remaining, current_text)
    # new_meal = "🍽 Déjeuner (Xkcal | ...)\ndescription"
    new_desc_lines = new_meal.split("\n")[1:]
    new_desc = "\n".join(new_desc_lines).strip()

    # Reconstruit le texte complet en conservant les macros actuelles des autres repas
    meal_key_map = {"petit_dejeuner": "petit_dej", "dejeuner": "dejeuner",
                    "collation": "collation", "diner": "diner"}
    import re as _re
    EMOJI_KEYS = {"🌅": "petit_dej", "🍽": "dejeuner", "🍎": "collation", "🌙": "diner"}
    existing_macros = {}
    for em, key in EMOJI_KEYS.items():
        m = _re.search(rf'{_re.escape(em)}[^\(]*\((\d+)kcal\s*\|\s*(\d+)g\s*P\s*\|\s*(\d+)g\s*G\s*\|\s*(\d+)g\s*L\)', current_text)
        if m:
            existing_macros[key] = {"kcal": int(m.group(1)), "p": int(m.group(2)),
                                    "g": int(m.group(3)), "l": int(m.group(4))}
        else:
            existing_macros[key] = ai_mod._split_macros(total_targets)[key]

    # Écrase le repas cible avec les vrais macros estimés par l'IA (depuis new_meal header)
    target_key = meal_key_map[meal_type]
    new_meal_header = new_meal.split("\n")[0]
    nm = _re.search(r'\((\d+)kcal\s*\|\s*(\d+)g\s*P\s*\|\s*(\d+)g\s*G\s*\|\s*(\d+)g\s*L\)', new_meal_header)
    if nm:
        existing_macros[target_key] = {"kcal": int(nm.group(1)), "p": int(nm.group(2)),
                                       "g": int(nm.group(3)), "l": int(nm.group(4))}
    else:
        existing_macros[target_key] = {"kcal": remaining["calories"], "p": remaining["protein_g"],
                                       "g": remaining["carbs_g"],     "l": remaining["fat_g"]}

    # Reconstruit les descriptions en remplaçant la cible
    descs = _extract_meal_descriptions(current_text)
    descs[meal_type] = new_desc

    # Calcule le vrai total (somme des 4 repas avec vrais macros)
    m = existing_macros
    total_kcal = sum(v["kcal"] for v in m.values())
    total_p    = sum(v["p"]    for v in m.values())
    total_g    = sum(v["g"]    for v in m.values())
    total_l    = sum(v["l"]    for v in m.values())

    updated_text = (
        f"🌅 Petit-déjeuner ({m['petit_dej']['kcal']}kcal | {m['petit_dej']['p']}g P | {m['petit_dej']['g']}g G | {m['petit_dej']['l']}g L)\n"
        f"{ai_mod._clean_desc(descs.get('petit_dejeuner', ''))}\n\n"
        f"🍽 Déjeuner ({m['dejeuner']['kcal']}kcal | {m['dejeuner']['p']}g P | {m['dejeuner']['g']}g G | {m['dejeuner']['l']}g L)\n"
        f"{ai_mod._clean_desc(descs.get('dejeuner', ''))}\n\n"
        f"🍎 Collation 17h30 ({m['collation']['kcal']}kcal | {m['collation']['p']}g P | {m['collation']['g']}g G | {m['collation']['l']}g L)\n"
        f"{ai_mod._clean_desc(descs.get('collation', ''))}\n\n"
        f"🌙 Dîner ({m['diner']['kcal']}kcal | {m['diner']['p']}g P | {m['diner']['g']}g G | {m['diner']['l']}g L)\n"
        f"{ai_mod._clean_desc(descs.get('diner', ''))}\n\n"
        f"Total estimé : {total_kcal}kcal | {total_p}g P | {total_g}g G | {total_l}g L"
    )
    db.save_daily_plan(week_start, weekday, updated_text, user_id=uid)

    # Mode couple : resynchronise le partenaire avec le nouveau repas
    partner_uid = db.get_partner_user_id(session["user_id"])
    if partner_uid and partner_uid != uid:
        p_partner = db.get_profile(user_id=partner_uid)
        if p_partner:
            p_partner["extra_sports"] = parse_extra_sports(p_partner.get("extra_sports"))
            _apply_partner_macros_to_existing_meals(p_partner, week_start, partner_uid, uid)

    return jsonify({"ok": True, "meals": updated_text, "new_meal": new_meal})


def _replace_meal_section(full_text: str, meal_type: str, new_meal: str) -> str:
    """Remplace une section repas via regex — robuste contre les emojis dans les descriptions."""
    import re
    markers      = {"petit_dejeuner": "🌅", "dejeuner": "🍽", "collation": "🍎", "diner": "🌙"}
    next_markers = {"petit_dejeuner": "🍽", "dejeuner": "🍎", "collation": "🌙", "diner": None}

    target = markers.get(meal_type)
    if not target:
        return full_text

    next_m = next_markers[meal_type]
    lookahead = f'(?={re.escape(next_m)})' if next_m else r'(?=Total estimé|\Z)'

    # Matche depuis le header de la section jusqu'au début de la suivante (ou fin)
    pattern = re.compile(
        rf'{re.escape(target)}[^\n]*\n[\s\S]*?{lookahead}',
        re.MULTILINE
    )

    # new_meal peut contenir l'emoji dans la description — on le nettoie
    lines = new_meal.split("\n")
    header = lines[0]
    desc   = "\n".join(
        l for l in lines[1:] if not any(l.startswith(e) for e in markers.values())
    ).strip()
    clean_meal = f"{header}\n{desc}\n\n" if desc else f"{header}\n\n"

    result = pattern.sub(clean_meal, full_text)
    if result == full_text:
        # Section absente : on l'ajoute à la fin
        result = full_text.rstrip() + "\n\n" + clean_meal
    return result


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
    from ai import _call, HAIKU, _split_macros, _build_meal_text
    import json as _json

    uid = current_user_id()
    main_uid = session["user_id"]
    data = request.get_json()
    day_num = data.get("day")
    if day_num is None or not (0 <= day_num <= 6):
        return jsonify({"ok": False, "error": "day invalide"}), 400

    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    targets = _get_targets(p, day_num)
    day_name = DAYS_FR[day_num]

    try:
        # Génère les descriptions une seule fois
        prompt = f"""Propose 4 descriptions de repas pour {day_name}.
Cuisine variée et moderne : méditerranéen, asiatique, mexicain, libanais, américain healthy, français revisité.
Noms de plats concrets et appétissants avec ingrédients principaux et quantités en grammes.

Réponds UNIQUEMENT en JSON :
{{"petit_dejeuner": "...", "dejeuner": "...", "collation": "...", "diner": "..."}}"""
        raw = _call(prompt, model=HAIKU, max_tokens=400)
        try:
            s, e = raw.find("{"), raw.rfind("}") + 1
            descriptions = _json.loads(raw[s:e])
        except Exception:
            descriptions = {"petit_dejeuner": "Œufs brouillés + pain complet", "dejeuner": "Poulet grillé + riz",
                            "collation": "Yaourt grec + fruits", "diner": "Saumon + patates douces"}

        macros = _split_macros(targets)
        meals = _build_meal_text(descriptions, macros, targets)

        # Ne pas sauvegarder ici — le wizard stocke le texte côté JS
        # La synchro partenaire se fait lors du "Générer les courses" (generate_shopping_from_plan)
        return jsonify({"ok": True, "meals": meals})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/suggest_week_meals", methods=["POST"])
def suggest_week_meals():
    """Génère les repas des 7 jours en un seul appel IA. Retourne la liste des textes."""
    uid = current_user_id()
    main_uid = session["user_id"]
    data = request.get_json()
    week_start = data.get("week_start")

    p = db.get_profile(user_id=uid)
    if not p:
        return jsonify({"ok": False, "error": "Profil manquant"}), 400

    # Mode couple : générer aussi pour le partenaire avec mêmes plats
    partner_uid = db.get_partner_user_id(main_uid)
    partner_profile = None
    if partner_uid and partner_uid != uid:
        partner_profile = db.get_profile(user_id=partner_uid)
        if partner_profile:
            partner_profile["extra_sports"] = parse_extra_sports(partner_profile.get("extra_sports"))

    try:
        if week_start:
            date.fromisoformat(week_start)
        else:
            week_start = db.get_next_week_start()

        p["extra_sports"] = parse_extra_sports(p.get("extra_sports"))
        _generate_week_meals_only(p, week_start, user_id=uid,
                                   partner_profile=partner_profile, partner_user_id=partner_uid if partner_uid != uid else None)
        texts = [db.get_daily_plan(week_start, d, user_id=uid) or "" for d in range(7)]
        return jsonify({"ok": True, "texts": texts})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


def _scale_quantities(text: str, ratio: float) -> str:
    """Scale toutes les quantités (g, ml, cl) d'un texte par un ratio calorique, arrondi au 5g près."""
    import re
    if abs(ratio - 1.0) < 0.02:
        return text
    def _replace(m):
        scaled = max(5, round(float(m.group(1)) * ratio / 5) * 5)
        return f"{scaled}{m.group(2)}"
    return re.sub(r'(\d+(?:\.\d+)?)\s*(g|ml|cl)\b', _replace, text)


def _meal_text_for_shopping(text: str) -> str:
    """Retourne une version compacte du texte journalier : descriptions seules, sans lignes de macros.
    Réduit drastiquement la taille envoyée à l'IA pour les courses."""
    LABELS = {"🌅": "Petit-déj", "🍽": "Déjeuner", "🍎": "Collation", "🌙": "Dîner"}
    lines_out = []
    for line in text.split("\n"):
        # Ligne de titre repas → garder juste l'emoji + label sans les macros
        matched = next((e for e in LABELS if line.startswith(e)), None)
        if matched:
            lines_out.append(f"{matched} {LABELS[matched]}")
            continue
        # Ligne de macros (Total estimé) ou vide → ignorer
        if line.startswith("Total estimé") or not line.strip():
            continue
        # Description du plat → garder
        lines_out.append(line.strip())
    return "\n".join(lines_out)


def _extract_meal_descriptions(text: str) -> dict:
    """Extrait les descriptions des plats (sans les lignes de macros) d'un texte journalier."""
    MEAL_EMOJIS = {"🌅": "petit_dejeuner", "🍽": "dejeuner", "🍎": "collation", "🌙": "diner"}
    descriptions = {}
    current_key = None
    body_lines = []
    for line in text.split("\n"):
        emoji_key = next((v for e, v in MEAL_EMOJIS.items() if line.startswith(e)), None)
        if emoji_key:
            if current_key and body_lines:
                descriptions[current_key] = "\n".join(body_lines).strip()
            current_key = emoji_key
            body_lines = []
        elif current_key and not line.startswith("Total estimé"):
            body_lines.append(line)
    if current_key and body_lines:
        descriptions[current_key] = "\n".join(body_lines).strip()
    return descriptions


def _generate_week_meals_only(profile: dict, week_start: str, user_id: int = 1,
                               from_day: int = 0,
                               partner_profile: dict = None, partner_user_id: int = None):
    """Appel IA unique : génère et sauvegarde les repas sans toucher aux courses.
    from_day : 0=Lundi … 6=Dimanche — seuls les jours >= from_day sont générés.
    L'IA génère uniquement les descriptions, Python injecte les macros correctes par jour.
    Si partner_profile est fourni, génère aussi les repas du partenaire (mêmes plats, macros adaptées).
    """
    from ai import _call, HAIKU, _split_macros, _build_meal_text

    macros_training = profile.get("macros_training")
    macros_rest_raw = profile.get("macros_rest")
    targets_train = json.loads(macros_training) if isinstance(macros_training, str) else (macros_training or prof.get_daily_targets(profile, is_rest_day=False))
    targets_rest  = json.loads(macros_rest_raw) if isinstance(macros_rest_raw, str) else (macros_rest_raw or prof.get_daily_targets(profile, is_rest_day=True))
    rest_days_set = set(parse_rest_days(profile.get("rest_days", [])))

    # Macros partenaire
    if partner_profile and partner_user_id:
        p_macros_training = partner_profile.get("macros_training")
        p_macros_rest_raw = partner_profile.get("macros_rest")
        p_targets_train = json.loads(p_macros_training) if isinstance(p_macros_training, str) else (p_macros_training or prof.get_daily_targets(partner_profile, is_rest_day=False))
        p_targets_rest  = json.loads(p_macros_rest_raw) if isinstance(p_macros_rest_raw, str) else (p_macros_rest_raw or prof.get_daily_targets(partner_profile, is_rest_day=True))
        p_rest_days_set = set(parse_rest_days(partner_profile.get("rest_days", [])))

    days_needed = list(range(from_day, 7))  # ex. [3,4,5,6] si démarrage jeudi

    prompt = f"""Génère des descriptions de repas variés et modernes pour les jours {', '.join(str(d) for d in days_needed)} (0=Lundi … 6=Dimanche).
Cuisine variée : méditerranéen, asiatique (thaï, japonais, coréen), mexicain, libanais, américain healthy, français revisité.
Chaque jour doit avoir une identité culinaire différente. Noms de plats concrets avec ingrédients principaux et quantités en grammes.
Variété de protéines (poulet, bœuf, saumon, crevettes, thon, œufs, tofu). Évite de répéter le même plat.

Réponds UNIQUEMENT en JSON valide, tableau de {len(days_needed)} objet(s) :
[
  {{"day": {days_needed[0]}, "petit_dejeuner": "description...", "dejeuner": "description...", "collation": "description...", "diner": "description..."}},
  ...
]"""

    raw = _call(prompt, model=HAIKU, max_tokens=3000)
    try:
        s = raw.find("[")
        e = raw.rfind("]") + 1
        entries = json.loads(raw[s:e])
    except Exception:
        entries = []

    default_desc = {
        "petit_dejeuner": "Œufs brouillés + pain complet + fromage blanc",
        "dejeuner": "Poulet grillé + riz complet + légumes vapeur",
        "collation": "Yaourt grec + fruits rouges + amandes",
        "diner": "Saumon + patates douces + brocoli",
    }
    entries_by_day = {int(e.get("day", -1)): e for e in entries if e.get("day") is not None}

    for day in days_needed:
        entry = entries_by_day.get(day, {"day": day, **default_desc})

        # Profil principal
        is_rest = day in rest_days_set
        targets = targets_rest if is_rest else targets_train
        macros = _split_macros(targets)
        text = _build_meal_text(entry, macros, targets)
        db.save_daily_plan(week_start, day, text, user_id=user_id)

        # Partenaire (mêmes plats, quantités et macros adaptées)
        if partner_profile and partner_user_id:
            p_is_rest = day in p_rest_days_set
            p_targets = p_targets_rest if p_is_rest else p_targets_train
            p_macros = _split_macros(p_targets)
            # Scale les quantités en grammes proportionnellement au ratio calorique
            ratio = p_targets['calories'] / targets['calories'] if targets.get('calories') else 1.0
            scaled_entry = {k: _scale_quantities(v, ratio) if isinstance(v, str) else v
                           for k, v in entry.items()}
            p_text = _build_meal_text(scaled_entry, p_macros, p_targets)
            db.save_daily_plan(week_start, day, p_text, user_id=partner_user_id)


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

    # Mode couple : génère aussi les repas du partenaire (mêmes plats)
    partner_uid = db.get_partner_user_id(uid)
    partner_profile = None
    if partner_uid:
        partner_profile = db.get_profile(user_id=partner_uid)
        if partner_profile:
            partner_profile["extra_sports"] = parse_extra_sports(partner_profile.get("extra_sports"))

    try:
        _generate_week_meals_only(p, week_start, user_id=uid, from_day=from_day,
                                   partner_profile=partner_profile, partner_user_id=partner_uid)

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
            meals_summary += f"{DAYS_FR[int(day_num)]} :\n{_meal_text_for_shopping(text)}\n\n"

    if not meals_summary:
        return jsonify({"ok": False, "error": "Aucun repas renseigné"}), 400

    # Mode couple : resynchroniser les repas du partenaire depuis les repas du profil actif
    # (garantit que les deux profils ont les mêmes plats, même si le wizard a été utilisé jour par jour)
    main_uid = session["user_id"]
    partner_uid = db.get_partner_user_id(main_uid)
    # Toujours recalculer depuis le profil principal (pas le partenaire actif)
    source_uid  = uid  # profil dont on vient de sauvegarder les repas
    target_uid  = partner_uid if partner_uid != uid else None
    if not target_uid:
        # Si on est sur le profil partenaire, la source est le profil principal
        other = db.get_partner_user_id(uid)  # will be None or main
        source_uid = session["user_id"]
        target_uid = other if other != session["user_id"] else None

    if target_uid:
        p_partner = db.get_profile(user_id=target_uid)
        if p_partner:
            p_partner["extra_sports"] = parse_extra_sports(p_partner.get("extra_sports"))
            _apply_partner_macros_to_existing_meals(p_partner, week_start, target_uid, source_uid)

    partner_meals_summary = ""
    if partner_uid:
        for day_num in range(7):
            p_text = db.get_daily_plan(week_start, day_num, user_id=partner_uid)
            if p_text:
                partner_meals_summary += f"{DAYS_FR[day_num]} :\n{_meal_text_for_shopping(p_text)}\n\n"

    combined_summary = meals_summary
    if partner_meals_summary:
        combined_summary = (
            "=== Profil 1 ===\n" + meals_summary +
            "\n=== Profil 2 ===\n" + partner_meals_summary
        )

    try:
        shopping = generate_shopping_from_meals(combined_summary, is_couple=bool(partner_uid))
        db.save_weekly_shopping(week_start, shopping, user_id=uid)
        if partner_uid:
            db.save_weekly_shopping(week_start, shopping, user_id=partner_uid)
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
    port = int(os.getenv("WEB_PORT", 5001))
    app.run(host="0.0.0.0", port=port, debug=False)
