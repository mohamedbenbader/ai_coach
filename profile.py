"""
Calculs nutritionnels — zéro IA, pure math.
Formule Mifflin-St Jeor + Harris-Benedict pour TDEE.
"""
import json

ACTIVITY_MULTIPLIERS = {
    "sedentaire": 1.2,
    "leger": 1.375,
    "modere": 1.55,
    "actif": 1.725,
    "tres_actif": 1.9,
}

JOB_BASE_MULTIPLIER = {
    "bureau":    1.20,  # assis toute la journée (bureau, télétravail)
    "maison":    1.22,  # étudiant / parent au foyer / retraité
    "debout":    1.38,  # debout sur place (cuisinier, coiffeur, caissier)
    "mouvement": 1.55,  # sur le terrain toute la journée (prof, infirmier, facteur)
    "physique":  1.75,  # effort physique intense (chantier, manutention)
}

SPORT_SESSION_MULTIPLIER = {
    "running":       0.045,
    "velo":          0.040,
    "natation":      0.045,
    "yoga":          0.020,
    "pilates":       0.020,
    "danse":         0.025,
    "football":      0.040,
    "arts_martiaux": 0.045,
    "crossfit":      0.055,
    "tennis":        0.035,
}

# Kept for backward compatibility only
SPORT_EXTRA_MULTIPLIER = {
    k: v * 2 for k, v in SPORT_SESSION_MULTIPLIER.items()
}


def parse_extra_sports(raw) -> list:
    """Normalise extra_sports quel que soit le format (list, JSON string)."""
    if isinstance(raw, list):
        return raw or []
    if isinstance(raw, str):
        try:
            return json.loads(raw) if raw else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def parse_rest_days(raw) -> list:
    """Normalise rest_days quel que soit le format (list, JSON string) → liste d'entiers 0-6."""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        try:
            items = json.loads(raw) if raw else []
        except (json.JSONDecodeError, ValueError):
            items = []
    else:
        items = []
    return sorted(set(int(d) for d in items if 0 <= int(d) <= 6))


def derive_activity_multiplier(job_type: str, gym_sessions: int, extra_sports) -> float:
    """Calcule le multiplicateur TDEE depuis le type de boulot, séances salle et sports additionnels.
    extra_sports peut être :
      - une liste de dicts [{"sport": "running", "sessions": 2}, ...]
      - une chaîne (ancien format) : "running"
    """
    base = JOB_BASE_MULTIPLIER.get(job_type, 1.20)
    gym  = min(int(gym_sessions) * 0.04, 0.24)

    sport_bonus = 0.0
    if isinstance(extra_sports, str):
        # Ancien format : une seule sport, on suppose 2 séances/sem
        per_session = SPORT_SESSION_MULTIPLIER.get(extra_sports, 0.0)
        sport_bonus = per_session * 2
    elif isinstance(extra_sports, list):
        for item in extra_sports:
            sport    = item.get("sport", "aucun")
            sessions = min(int(item.get("sessions", 1)), 7)
            per_session = SPORT_SESSION_MULTIPLIER.get(sport, 0.0)
            sport_bonus += per_session * sessions

    return min(base + gym + sport_bonus, 2.50)

# Jours d'entraînement par défaut (0=lundi ... 6=dimanche)
DEFAULT_TRAINING_DAYS = [0, 1, 2, 3, 5, 6]  # L Ma Me J Sa Di (vendredi = repos)


def calculate_bmr(weight_kg: float, height_cm: int, age: int, gender: str = "male") -> float:
    """Mifflin-St Jeor BMR"""
    if gender == "male":
        return 10 * weight_kg + 6.25 * height_cm - 5 * age + 5
    return 10 * weight_kg + 6.25 * height_cm - 5 * age - 161


def calculate_tdee(weight_kg: float, height_cm: int, age: int,
                   activity_level: str = "modere", gender: str = "male") -> float:
    bmr = calculate_bmr(weight_kg, height_cm, age, gender)
    multiplier = ACTIVITY_MULTIPLIERS.get(activity_level, 1.55)
    return bmr * multiplier


def calculate_macros(tdee: float, goal: str = "recomposition", is_rest_day: bool = False) -> dict:
    """
    Calcule les macros selon l'objectif.
    goal: 'recomposition' | 'perte_gras' | 'prise_masse'
    """
    if goal == "recomposition":
        calories = tdee - 300 if not is_rest_day else tdee - 600
    elif goal == "perte_gras":
        calories = tdee - 500 if not is_rest_day else tdee - 700
    elif goal == "prise_masse":
        calories = tdee + 200 if not is_rest_day else tdee
    else:
        calories = tdee - 300

    calories = round(calories)

    # Protéines : 2.2g/kg de poids corporel (à ajuster via profil)
    # Ces valeurs sont recalculées avec le vrai poids dans get_daily_targets()
    protein_g = round(calories * 0.32 / 4)
    fat_g = round(calories * 0.27 / 9)
    carbs_g = round((calories - protein_g * 4 - fat_g * 9) / 4)

    return {
        "calories": calories,
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
    }


def get_daily_targets(profile: dict, is_rest_day: bool = False) -> dict:
    """Retourne les objectifs nutritionnels du jour selon le profil."""
    weight   = profile["weight_kg"]
    height   = profile["height_cm"]
    age      = profile["age"]
    sexe     = profile.get("sexe", "homme")
    gender   = "male" if sexe == "homme" else "female"
    goal     = profile.get("goal", "recomposition")

    # Utilise les champs détaillés si disponibles, sinon activity_level
    job_type     = profile.get("job_type")
    gym_sessions = profile.get("gym_sessions_per_week")

    extra_sports = parse_extra_sports(profile.get("extra_sports"))

    bmr = calculate_bmr(weight, height, age, gender)
    if job_type is not None and gym_sessions is not None:
        multiplier = derive_activity_multiplier(job_type, int(gym_sessions), extra_sports)
        tdee = bmr * multiplier
    else:
        activity   = profile.get("activity_level", "modere")
        multiplier = ACTIVITY_MULTIPLIERS.get(activity, 1.55)
        tdee       = bmr * multiplier

    macros = calculate_macros(tdee, goal=goal, is_rest_day=is_rest_day)

    # Affiner les protéines : 2.2g/kg
    protein_g = round(weight * 2.2)
    fat_g = round(macros["calories"] * 0.27 / 9)
    carbs_g = round((macros["calories"] - protein_g * 4 - fat_g * 9) / 4)

    return {
        "calories": macros["calories"],
        "protein_g": protein_g,
        "carbs_g": carbs_g,
        "fat_g": fat_g,
        "is_rest_day": is_rest_day,
    }


def weeks_to_goal(profile: dict) -> dict:
    """Estime le nombre de semaines pour atteindre l'objectif de poids."""
    weight      = float(profile.get("weight_kg", 80))
    goal_weight = float(profile.get("goal_weight_kg", 75))
    goal        = profile.get("goal", "recomposition")

    diff = weight - goal_weight  # positif = perte, négatif = prise de masse

    if goal == "perte_gras":
        kg_per_week = 0.50   # déficit ~500 kcal/jour
    elif goal == "prise_masse":
        kg_per_week = 0.20   # surplus ~200 kcal/jour
    else:
        kg_per_week = 0.30   # recomposition progressive

    if abs(diff) < 0.5:
        weeks = 8   # déjà à l'objectif, consolidation
        status = "maintenance"
    else:
        weeks = max(4, round(abs(diff) / kg_per_week))
        status = "perte" if diff > 0 else "prise"

    # Nombre de cycles de 4 semaines nécessaires
    phases = max(1, round(weeks / 4))

    return {
        "weeks":      weeks,
        "months":     round(weeks / 4.33, 1),
        "phases":     phases,          # nombre de cycles de 4 semaines
        "kg_diff":    round(abs(diff), 1),
        "kg_per_week": kg_per_week,
        "status":     status,
    }


def get_weekly_weight_trend(weight_logs: list[dict]) -> dict:
    """Analyse la tendance de poids sur les dernières semaines."""
    if len(weight_logs) < 2:
        return {"trend": "insufficient_data", "weekly_change": 0}

    sorted_logs = sorted(weight_logs, key=lambda x: x["logged_at"])
    latest = sorted_logs[-1]["weight_kg"]
    oldest = sorted_logs[0]["weight_kg"]
    weeks = max(1, len(sorted_logs) / 7)
    weekly_change = (latest - oldest) / weeks

    if weekly_change < -0.6:
        trend = "perte_trop_rapide"
    elif weekly_change < -0.1:
        trend = "en_bonne_voie"
    elif weekly_change < 0.1:
        trend = "stable"
    else:
        trend = "prise_de_poids"

    return {
        "trend": trend,
        "weekly_change": round(weekly_change, 2),
        "current_weight": latest,
        "start_weight": oldest,
        "total_change": round(latest - oldest, 2),
    }


def format_macros_message(targets: dict) -> str:
    label = "Jour de repos" if targets["is_rest_day"] else "Jour d'entraînement"
    return (
        f"🎯 *{label}*\n"
        f"Calories: {targets['calories']} kcal\n"
        f"Protéines: {targets['protein_g']}g\n"
        f"Glucides: {targets['carbs_g']}g\n"
        f"Lipides: {targets['fat_g']}g"
    )
