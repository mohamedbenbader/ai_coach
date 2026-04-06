"""
Centralise tous les appels à l'API Anthropic.
Utilise claude-haiku-4-5 (le moins cher) pour tout sauf les bilans.
Principe : prompts courts, contexte minimal, pas de répétition.
"""
import os
import json
import copy
import logging
import anthropic
from dotenv import load_dotenv
from profile import parse_extra_sports, parse_rest_days
from training import DAYS_FR

load_dotenv()

client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-4-6"


def _call(prompt: str, model: str = HAIKU, max_tokens: int = 800) -> str:
    message = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text



def _split_macros(t: dict) -> dict:
    """Répartit les macros journalières en 4 repas (ratios fixes)."""
    ratios = {"petit_dej": 0.23, "dejeuner": 0.32, "collation": 0.12, "diner": 0.33}
    result = {}
    for meal, ratio in ratios.items():
        result[meal] = {
            "kcal": round(t["calories"] * ratio),
            "p":    round(t["protein_g"] * ratio),
            "g":    round(t["carbs_g"]   * ratio),
            "l":    round(t["fat_g"]     * ratio),
        }
    return result


def _build_meal_text(descriptions: dict, macros: dict, targets: dict) -> str:
    """Assemble le texte d'un jour en injectant les macros corrects (pas l'IA)."""
    m = macros
    return (
        f"🌅 Petit-déjeuner ({m['petit_dej']['kcal']}kcal | {m['petit_dej']['p']}g P | {m['petit_dej']['g']}g G | {m['petit_dej']['l']}g L)\n"
        f"{descriptions.get('petit_dejeuner', '').strip()}\n\n"
        f"🍽 Déjeuner ({m['dejeuner']['kcal']}kcal | {m['dejeuner']['p']}g P | {m['dejeuner']['g']}g G | {m['dejeuner']['l']}g L)\n"
        f"{descriptions.get('dejeuner', '').strip()}\n\n"
        f"🍎 Collation 17h30 ({m['collation']['kcal']}kcal | {m['collation']['p']}g P | {m['collation']['g']}g G | {m['collation']['l']}g L)\n"
        f"{descriptions.get('collation', '').strip()}\n\n"
        f"🌙 Dîner ({m['diner']['kcal']}kcal | {m['diner']['p']}g P | {m['diner']['g']}g G | {m['diner']['l']}g L)\n"
        f"{descriptions.get('diner', '').strip()}\n\n"
        f"Total estimé : {targets['calories']}kcal | {targets['protein_g']}g P | {targets['carbs_g']}g G | {targets['fat_g']}g L"
    )


def generate_daily_meals(targets: dict, day_name: str,
                         previous_meals: list[str] = None) -> str:
    """Génère le plan repas du jour. L'IA choisit les plats, Python fixe les macros."""
    avoid = f"Évite : {', '.join(previous_meals[:6])}." if previous_meals else ""

    prompt = f"""Propose 4 descriptions de repas pour {day_name}. {avoid}
Cuisine variée et moderne : méditerranéen, asiatique, mexicain, libanais, américain healthy, français revisité.
Noms de plats concrets et appétissants avec ingrédients principaux et quantités en grammes (ex: "Saumon grillé 150g + riz basmati 80g + courgettes sautées 120g").

Réponds UNIQUEMENT en JSON :
{{"petit_dejeuner": "...", "dejeuner": "...", "collation": "...", "diner": "..."}}"""

    raw = _call(prompt, model=HAIKU, max_tokens=400)
    try:
        start, end = raw.find("{"), raw.rfind("}") + 1
        descriptions = json.loads(raw[start:end])
    except Exception:
        descriptions = {
            "petit_dejeuner": "Œufs brouillés + pain complet + fromage blanc",
            "dejeuner": "Poulet grillé + riz complet + légumes vapeur",
            "collation": "Yaourt grec + fruits rouges + amandes",
            "diner": "Saumon + patates douces + brocoli",
        }

    macros = _split_macros(targets)
    return _build_meal_text(descriptions, macros, targets)


def regenerate_single_meal(meal_type: str, day_name: str,
                           remaining: dict, other_meals: str = "") -> str:
    """
    Régénère un seul repas en respectant :
    - Le budget macros restant après les autres repas
    """
    meal_labels = {
        "petit_dejeuner": ("Petit-déjeuner", "🌅"),
        "dejeuner":       ("Déjeuner",       "🍽"),
        "collation":      ("Collation 17h30", "🍎"),
        "diner":          ("Dîner",           "🌙"),
    }
    label, emoji = meal_labels.get(meal_type, ("Repas", "🍽"))

    avoid = f"\nÉvite de répéter : {other_meals[:200]}" if other_meals else ""

    prompt = f"""Propose uniquement la description du {label} pour {day_name}.{avoid}
Cuisine variée et moderne : méditerranéen, asiatique, mexicain, libanais, américain healthy, français revisité.
Donne un plat concret avec ingrédients et quantités (ex: "Poulet tikka masala 180g + riz basmati 80g + naan 30g").
Réponds UNIQUEMENT avec la description, sans macros, sans intro, 1-2 lignes max."""

    description = _call(prompt, model=HAIKU, max_tokens=100).strip()
    # On injecte les macros corrects nous-mêmes
    return (
        f"{emoji} {label} ({remaining['calories']}kcal | {remaining['protein_g']}g P | "
        f"{remaining['carbs_g']}g G | {remaining['fat_g']}g L)\n{description}"
    )


def generate_exercise_substitutes(exercise_name: str, session_name: str) -> list[dict]:
    """Propose 3 exercices alternatifs pour le même groupe musculaire."""
    prompt = f"""L'utilisateur ne peut pas faire "{exercise_name}" (séance : {session_name}).
Propose 3 exercices alternatifs qui ciblent le même groupe musculaire.
Réponds UNIQUEMENT en JSON :
[
  {{"name": "Nom exercice", "sets": "3x12", "rest": "90s", "why": "raison courte"}},
  ...
]"""
    raw = _call(prompt, model=HAIKU, max_tokens=300)
    try:
        start, end = raw.find("["), raw.rfind("]") + 1
        return json.loads(raw[start:end])
    except Exception:
        return []


def generate_weekly_report(profile: dict, stats: dict) -> str:
    """Génère un bilan hebdomadaire IA à partir des stats de la semaine."""
    name = profile.get("name", "")
    goal_labels = {
        "recomposition": "recomposition corporelle",
        "perte_gras": "perte de gras",
        "prise_masse": "prise de masse",
    }
    goal = goal_labels.get(profile.get("goal", "recomposition"), "recomposition")

    sessions_done  = stats.get("sessions_done", 0)
    sessions_total = stats.get("sessions_total", 0)
    weight_change  = stats.get("weight_change")
    current_weight = stats.get("current_weight")
    goal_weight    = profile.get("goal_weight_kg")
    meals_planned  = stats.get("meals_planned", 0)

    weight_line = ""
    if weight_change is not None:
        sign = "+" if weight_change > 0 else ""
        weight_line = f"- Poids : {current_weight} kg ({sign}{weight_change:+.1f} kg cette semaine)"

    prompt = f"""Tu es coach sportif. Génère un bilan hebdomadaire motivant et concret pour {name}.

Données de la semaine :
- Objectif : {goal} (cible : {goal_weight} kg)
- Séances réalisées : {sessions_done}/{sessions_total}
- Jours de repas planifiés : {meals_planned}/7
{weight_line}

Format :
**Bilan de la semaine**
[2-3 phrases d'analyse honnête et encourageante]

**Points positifs**
- point 1
- point 2

**À améliorer**
- point 1

**Conseil pour la semaine prochaine**
[1 conseil actionnable et précis]"""

    return _call(prompt, model=HAIKU, max_tokens=400)


def exercise_name_to_english(name: str) -> str:
    """Traduit un nom d'exercice français en anglais pour la recherche wger."""
    prompt = f'Translate this gym exercise name to English (2-4 words max, no explanation): "{name}"'
    return _call(prompt, model=HAIKU, max_tokens=20).strip().strip('"').strip("'")


def generate_recipe(meal_description: str) -> str:
    """Génère une recette courte (ingrédients + étapes) pour un plat donné."""
    prompt = f"""Voici un repas : {meal_description}

Génère une recette rapide et pratique. Format EXACT :
**Ingrédients (1 personne)**
- ingrédient : quantité
(liste tous les ingrédients)

**Préparation**
1. étape courte
2. étape courte
(max 5 étapes, pratique et direct)

**Temps** : X min"""
    return _call(prompt, model=HAIKU, max_tokens=400)


def generate_and_store_week_plan(profile: dict, week_start: str = None, user_id: int = 1) -> str:
    """
    Flux repas en premier :
    Appel 1 : génère les 7 jours de repas (librement)
    Appel 2 : génère la liste de courses basée sur ces repas
    Stocke tout en DB. Retourne la liste de courses.
    """
    from db import save_daily_plan, save_weekly_shopping, get_week_start as _get_ws
    from profile import get_daily_targets

    if week_start is None:
        week_start = _get_ws()

    # Lire les besoins calculés à la sauvegarde du profil — source unique de vérité
    macros_training = profile.get("macros_training")
    macros_rest_raw = profile.get("macros_rest")
    targets_train = json.loads(macros_training) if isinstance(macros_training, str) else (macros_training or get_daily_targets(profile, is_rest_day=False))
    targets_rest  = json.loads(macros_rest_raw) if isinstance(macros_rest_raw, str) else (macros_rest_raw or get_daily_targets(profile, is_rest_day=True))
    rest_days_set = set(parse_rest_days(profile.get("rest_days", [])))

    # ── Appel 1 : l'IA génère UNIQUEMENT les descriptions, Python injecte les macros ──
    meals_prompt = f"""Génère des descriptions de repas variés pour 7 jours (Lundi=0 à Dimanche=6).
Cuisine variée et moderne : méditerranéen, asiatique (thaï, japonais, coréen), mexicain, libanais, américain healthy, français revisité.
Chaque jour doit avoir une identité culinaire différente. Noms de plats concrets avec ingrédients principaux et quantités en grammes.
Variété de protéines (poulet, bœuf, saumon, crevettes, thon, œufs, tofu, fromage blanc). Évite de répéter le même plat.

Réponds UNIQUEMENT en JSON valide, 7 objets :
[
  {{"day": 0, "petit_dejeuner": "description...", "dejeuner": "description...", "collation": "description...", "diner": "description..."}},
  ...
]"""

    def _parse_days(raw: str):
        try:
            start = raw.find("[")
            end = raw.rfind("]") + 1
            return json.loads(raw[start:end])
        except Exception:
            return []

    raw = _call(meals_prompt, model=HAIKU, max_tokens=3000)
    days = _parse_days(raw)

    # Fallback si JSON invalide
    if len(days) < 7:
        default_desc = {
            "petit_dejeuner": "Œufs brouillés + pain complet + fromage blanc",
            "dejeuner": "Poulet grillé + riz complet + légumes vapeur",
            "collation": "Yaourt grec + fruits rouges + amandes",
            "diner": "Saumon + patates douces + brocoli",
        }
        days = [{"day": d, **default_desc} for d in range(7)]

    # ── Python injecte les macros corrects — l'IA ne calcule plus rien ──
    meals_summary = ""
    for entry in days:
        day = entry.get("day")
        if day is None:
            continue
        is_rest = day in rest_days_set
        targets = targets_rest if is_rest else targets_train
        macros = _split_macros(targets)
        text = _build_meal_text(entry, macros, targets)
        save_daily_plan(week_start, day, text, user_id=user_id)
        meals_summary += f"Jour {day} :\n{entry.get('dejeuner', '')} / {entry.get('diner', '')}\n\n"

    # ── Appel 2 : liste de courses basée sur les repas ──
    shopping_prompt = f"""Voici le plan repas de la semaine :
{meals_summary[:2000]}

Génère la liste de courses complète pour ces 7 jours, groupée par catégorie avec quantités.
Concis, pas d'intro :
Viandes/Poissons :
- [item] — [quantité]
Œufs/Produits laitiers :
Légumes :
Féculents :
Fruits :
Épicerie :"""

    shopping = _call(shopping_prompt, model=HAIKU, max_tokens=700)
    save_weekly_shopping(week_start, shopping, user_id=user_id)

    return shopping




def _build_perf_hint(perf_summary: list, phase_number: int) -> str:
    """Construit le bloc de contexte performances à injecter dans le prompt IA."""
    if not perf_summary or phase_number <= 1:
        return ""
    lines = []
    for row in perf_summary:
        name    = row["exercise_name"]
        weight  = row.get("avg_weight")
        sets    = int(row.get("avg_sets") or 0)
        sessions = row.get("sessions", 0)
        if weight:
            lines.append(f"  - {name} : {weight} kg × {sets} séries ({sessions} séances loggées)")
        else:
            lines.append(f"  - {name} : {sets} séries ({sessions} séances loggées)")
    if not lines:
        return ""
    return (
        f"\nPERFORMANCES RÉELLES phase {phase_number - 1} (adapte les charges pour la phase {phase_number}) :\n"
        + "\n".join(lines)
        + "\n→ Augmente les charges des exercices bien maîtrisés, consolide ceux avec peu de séances.\n\n"
    )


def generate_training_program(profile: dict, phase_number: int = 1, total_phases: int = None, perf_summary: list = None) -> list[dict]:
    """
    Génère un programme d'entraînement progressif sur 4 semaines (28 sessions).
    Cycle intra-phase :
      Semaine 1 — Fondation  : apprentissage des mouvements, volume modéré (3x12-15)
      Semaine 2 — Volume     : augmentation des séries (4x10-12), mêmes exercices
      Semaine 3 — Intensité  : charges lourdes, moins de répétitions (4x6-8)
      Semaine 4 — Décharge   : récupération active (2-3x10), charges légères
    La progression ENTRE phases augmente les charges de ~8% par phase.
    """
    from profile import weeks_to_goal

    wtg = weeks_to_goal(profile)
    if total_phases is None:
        total_phases = wtg["phases"]

    # ── Variables de base du profil ──
    gym_sessions = int(profile.get("gym_sessions_per_week", 3))
    job_type     = profile.get("job_type", "bureau")
    sexe         = profile.get("sexe", "homme")

    goal_labels = {
        "recomposition": "recomposition corporelle (perte de gras + maintien musculaire)",
        "perte_gras": "perte de gras",
        "prise_masse": "prise de masse musculaire",
    }
    goal = goal_labels.get(profile.get("goal", "recomposition"), "recomposition corporelle")

    fitness_level = profile.get("fitness_level", "intermediaire")
    fitness_labels = {
        "debutant":      "Débutant — peu ou pas d'expérience en salle, mouvements de base à apprendre, charges très légères, repos longs",
        "intermediaire": "Intermédiaire — maîtrise les exercices fondamentaux, peut augmenter progressivement les charges",
        "avance":        "Avancé — bonne maîtrise technique, supporte un volume élevé, peut intégrer des techniques intensives (drop sets, supersets)",
        "expert":        "Expert — maîtrise complète, charges maximales, périodisation complexe, techniques avancées (rest-pause, cluster sets)",
    }
    fitness_str = fitness_labels.get(fitness_level, fitness_labels["intermediaire"])

    sport_labels = {
        "running": "Running", "velo": "Vélo", "natation": "Natation",
        "yoga": "Yoga", "pilates": "Pilates", "football": "Football/Basketball",
        "arts_martiaux": "Arts martiaux", "danse": "Danse",
        "crossfit": "CrossFit/HIIT", "tennis": "Tennis/Padel",
    }
    job_labels = {
        "bureau":    "assis toute la journée (bureau, télétravail, développeur)",
        "maison":    "étudiant / parent au foyer / retraité",
        "debout":    "debout sur place (cuisinier, coiffeur, caissier, serveur)",
        "mouvement": "sur le terrain toute la journée (prof, infirmier, technicien, facteur)",
        "physique":  "effort physique intense (chantier, manutention, déménageur)",
    }
    job_str = job_labels.get(job_type, job_type)

    extra_sports = parse_extra_sports(profile.get("extra_sports", []))

    # Construire le résumé des sports additionnels
    sport_parts = []
    total_extra_sessions = 0
    for item in extra_sports:
        s = item.get("sport", "")
        n = int(item.get("sessions", 1))
        if s and s != "aucun":
            sport_parts.append(f"{sport_labels.get(s, s)} {n}×/sem")
            total_extra_sessions += n
    extra_str = ", ".join(sport_parts) if sport_parts else "aucun"

    # Jours de repos choisis par l'utilisateur
    pref_rest_days = parse_rest_days(profile.get("rest_days", []))

    # Nombre de jours dédiés aux sports additionnels dans la semaine
    # Les sports peuvent prendre tous les jours non-muscu (pas de repos obligatoire)
    extra_days = min(total_extra_sessions, 7 - gym_sessions)
    rest_days  = 7 - gym_sessions - extra_days

    # Contrainte de repos pour le prompt
    if pref_rest_days:
        rest_days_str = ", ".join(DAYS_FR[d] for d in pref_rest_days)
        rest_constraint = (f"- Jours SANS MUSCULATION imposés par l'utilisateur : {rest_days_str}\n"
                           f"  → PAS de séance salle ces jours-là, mais un sport additionnel y est autorisé\n"
                           f"  → Si aucun sport assigné, ces jours ont exercises=[]")
    else:
        rest_constraint = f"- {rest_days} jour(s) de REPOS → exercises=[] OBLIGATOIRE"

    # Construire la contrainte détaillée pour l'IA
    if sport_parts:
        sport_constraint_lines = "\n".join(
            f"- {sport_labels.get(item['sport'], item['sport'])} : {item['sessions']}×/sem"
            for item in extra_sports if item.get("sport") not in ("aucun", None, "")
        )
        extra_constraint = (
            f"- Sports additionnels à placer dans la semaine :\n{sport_constraint_lines}\n"
            f"  (total ~{extra_days} jour(s) dédié(s) aux sports additionnels)"
        )
    else:
        extra_constraint = "- Pas de sport additionnel"

    # ── Progression des sports additionnels entre phases ──
    sport_phase_rules = {
        "running":      ["20-30 min footing facile, allure confort",
                         "35-40 min + séquences d'intervalles courts (30s vite/1min récup)",
                         "45-50 min tempo run ou intervals longs (1min vite/2min récup)",
                         "55-60 min, allure course, fractionnés intensifs"],
        "velo":         ["45 min plat, allure modérée",
                         "60 min + quelques côtes, cadence variée",
                         "75 min + intervalles en montée",
                         "90 min endurance, pointes de vitesse"],
        "natation":     ["1000 m, nage libre à allure confort",
                         "1400 m, alternance nages + séries courtes",
                         "1800 m, nage rapide + intervals 50 m sprint",
                         "2200 m, mixte nages, intensité maximale"],
        "yoga":         ["45 min flow doux, postures de base",
                         "50 min vinyasa intermédiaire, transitions fluides",
                         "55 min yoga power, équilibres et inversions",
                         "60 min séquence complète, postures avancées"],
        "pilates":      ["45 min Pilates mat débutant, stabilisation",
                         "50 min Pilates mat intermédiaire, gainage",
                         "55 min Pilates avancé, coordination",
                         "60 min séquence complète, contrôle total"],
        "football":     ["60 min jeu libre, cardio modéré",
                         "70 min + exercices techniques, sprints",
                         "80 min + circuits d'intensité, dribbles",
                         "90 min match entier, engagement maximal"],
        "arts_martiaux":["45 min kata/techniques de base",
                         "55 min sparring léger + kata enchaînements",
                         "65 min sparring intensif + puissance",
                         "75 min combat complet, pleine intensité"],
        "danse":        ["45 min chorégraphie simple, cardio léger",
                         "55 min chorégraphie intermédiaire, rythme",
                         "65 min chorégraphie intense, cardio élevé",
                         "75 min chorégraphie complète, endurance"],
        "crossfit":     ["20 min WOD léger, charges modérées, pas d'échec",
                         "25 min WOD modéré, +complexité mouvements",
                         "30 min WOD lourd, charges maximales",
                         "35 min WOD compétition, effort maximal"],
        "tennis":       ["45 min échanges à l'entraînement, cardio léger",
                         "60 min + jeux de points, déplacements",
                         "75 min matchs, déplacements intensifs",
                         "90 min match complet, pleine intensité"],
    }
    phase_idx = min(phase_number - 1, 3)
    sport_progression_hint = ""
    if extra_sports:
        lines = []
        for item in extra_sports:
            s = item.get("sport", "")
            if s in sport_phase_rules:
                rule = sport_phase_rules[s][phase_idx]
                lines.append(f"  • {sport_labels.get(s, s)} : {rule}")
        if lines:
            sport_progression_hint = (
                f"\nPROGRESSION SPORTS ADDITIONNELS phase {phase_number}/{total_phases} :"
                + "\n" + "\n".join(lines)
            )

    # ── Description de la phase courante ──
    if total_phases <= 1 or phase_number == 1:
        phase_desc = ("Phase 1 — Fondation absolue. Charges légères, maîtrise technique, bases solides."
                      + (" Sports additionnels : durée et intensité de base." if extra_sports else ""))
    elif phase_number == total_phases:
        pct = (phase_number - 1) * 8
        phase_desc = (f"Phase FINALE {phase_number}/{total_phases} — Pic d'intensité. "
                      f"Charges +{pct}% vs phase 1, techniques avancées (drop sets, supersets), push maximum."
                      + (" Sports additionnels au niveau performance maximal." if extra_sports else ""))
    else:
        pct = (phase_number - 1) * 8
        phase_desc = (f"Phase {phase_number}/{total_phases} — Progression continue. "
                      f"Charges +{pct}% vs phase 1, augmentation du volume et de l'intensité."
                      + (f" Sports additionnels plus longs et plus intenses qu'en phase {phase_number-1}." if extra_sports else ""))

    # ── Exemples d'exercices par sport (pour guider l'IA) ──
    _sport_ex = {
        "running":      ['{"name": "Échauffement — footing léger", "sets": "10 min", "rest": "—"}',
                         '{"name": "Fractionné 30s vite / 1min récup", "sets": "8 répétitions", "rest": "—"}',
                         '{"name": "Retour au calme — marche", "sets": "5 min", "rest": "—"}'],
        "velo":         ['{"name": "Échauffement en selle — allure facile", "sets": "10 min", "rest": "—"}',
                         '{"name": "Intervalles en côte / plat rapide", "sets": "6 répétitions", "rest": "—"}',
                         '{"name": "Récupération active — pédalage léger", "sets": "10 min", "rest": "—"}'],
        "natation":     ['{"name": "Échauffement nage libre", "sets": "200 m", "rest": "—"}',
                         '{"name": "Séries crawl 50 m rapide", "sets": "6 répétitions", "rest": "30s"}',
                         '{"name": "Dos crawlé récupération", "sets": "100 m", "rest": "—"}'],
        "yoga":         ['{"name": "Salutation au soleil", "sets": "5 cycles", "rest": "—"}',
                         '{"name": "Séquence debout — Guerrier I, II, III", "sets": "3 répétitions par côté", "rest": "—"}',
                         '{"name": "Équilibres — Arbre, Aigle", "sets": "2 répétitions par côté", "rest": "—"}',
                         '{"name": "Savasana — relaxation finale", "sets": "5 min", "rest": "—"}'],
        "pilates":      ['{"name": "Activation abdos profonds — respiration Pilates", "sets": "10 répétitions", "rest": "—"}',
                         '{"name": "The Hundred", "sets": "100 battements", "rest": "—"}',
                         '{"name": "Roll Up", "sets": "10 répétitions", "rest": "—"}',
                         '{"name": "Single Leg Circle", "sets": "8 répétitions par jambe", "rest": "—"}'],
        "football":     ['{"name": "Échauffement — passes courtes + jonglages", "sets": "10 min", "rest": "—"}',
                         '{"name": "Exercices techniques — dribbles slalom", "sets": "6 répétitions", "rest": "—"}',
                         '{"name": "Petits matchs ou jeu à thème", "sets": "3 périodes de 10 min", "rest": "2 min"}'],
        "arts_martiaux":['{"name": "Échauffement — shadow boxing", "sets": "5 min", "rest": "—"}',
                         '{"name": "Kata / enchaînements techniques", "sets": "10 répétitions", "rest": "—"}',
                         '{"name": "Sparring léger contrôlé", "sets": "3 rounds de 3 min", "rest": "1 min"}'],
        "danse":        ['{"name": "Échauffement — isolation articulaire", "sets": "5 min", "rest": "—"}',
                         '{"name": "Apprentissage chorégraphie", "sets": "3 répétitions complètes", "rest": "—"}',
                         '{"name": "Enchaînement en musique", "sets": "5 répétitions", "rest": "—"}'],
        "crossfit":     ['{"name": "Échauffement — mobilité + activation", "sets": "10 min", "rest": "—"}',
                         '{"name": "WOD : Burpees + Thrusters + Box Jumps", "sets": "AMRAP 15 min", "rest": "—"}',
                         '{"name": "Cool down — étirements", "sets": "5 min", "rest": "—"}'],
        "tennis":       ['{"name": "Échauffement — échanges de fond de court", "sets": "10 min", "rest": "—"}',
                         '{"name": "Exercices techniques — service + smash", "sets": "20 répétitions", "rest": "—"}',
                         '{"name": "Jeux de points — matchs courts", "sets": "3 sets de 6 jeux", "rest": "2 min"}'],
    }
    if extra_sports:
        ex_lines = []
        for item in extra_sports:
            s = item.get("sport", "")
            label = sport_labels.get(s, s)
            ex = _sport_ex.get(s, _sport_ex["running"])
            ex_lines.append(f"  Exemple {label} :\n" + "\n".join(f"    {e}" for e in ex))
        sport_examples_str = "\n".join(ex_lines)
        # Exemple JSON d'un jour de sport (premier sport de la liste)
        first = extra_sports[0]
        fs = first.get("sport", "")
        fs_label = sport_labels.get(fs, fs)
        fs_ex = _sport_ex.get(fs, _sport_ex["running"])
        fs_ex_json = ", ".join(fs_ex[:2])
        sport_day_json_example = (
            f'  {{"week_number": 1, "day_of_week": 2, "session_name": "{fs_label} S1", '
            f'"session_emoji": "🏃", "exercises": [{fs_ex_json}]}},\n'
        )
    else:
        sport_examples_str = "  (Pas de sport additionnel)"
        sport_day_json_example = ""

    prompt = f"""Crée un programme d'entraînement hebdomadaire personnalisé pour :
- {profile.get('name')}, {profile.get('age')} ans, {sexe}
- {profile.get('weight_kg')} kg / {profile.get('height_cm')} cm
- Objectif : {goal} (cible : {profile.get('goal_weight_kg')} kg)
- Niveau sportif : {fitness_str}
- Type de travail : {job_str}
- Séances salle/semaine : {gym_sessions}
- Sports additionnels : {extra_str}

PHASE ACTUELLE : {phase_desc}{sport_progression_hint}
→ Adapte les charges EN SALLE et les durées/intensités des sports additionnels selon les règles ci-dessus.
  La structure S1→S4 (Fondation→Volume→Intensité→Décharge) reste identique mais à l'intensité de la phase {phase_number}.

Contraintes STRICTES :
- Exactement {gym_sessions} jours de musculation en salle
{extra_constraint}
{rest_constraint}
- Répartition optimale sur la semaine (pas 2 jours muscu consécutifs si possible)
- Adapter les exercices à l'objectif ({goal})

RÈGLE ABSOLUE sur exercises[] :
• Jours MUSCULATION en salle : exercises avec au moins 5 exercices détaillés, format :
  {{"name": "Développé couché", "sets": "4x6-8", "rest": "2'30"}}
• Jours REPOS : exercises=[] — vide obligatoire
• Jours SPORT ADDITIONNEL : exercises avec 3 à 5 étapes PROPRES AU SPORT pratiqué.
{sport_examples_str}

⚠️ INTERDIT : Ne mets JAMAIS de "footing", "running", "jogging" ou exercices de course pour un jour de Yoga, Pilates, Natation ou tout autre sport NON-RUNNING.
⚠️ NOM DES SÉANCES : le session_name doit contenir le nom du sport pratiqué ce jour-là (ex: "Yoga S1", "Pilates S2", "Natation S3"...)

{_build_perf_hint(perf_summary, phase_number)}Objectif utilisateur : atteindre {profile.get('goal_weight_kg')} kg en ~{wtg['weeks']} semaines ({wtg['months']} mois) → {total_phases} phases de 4 semaines.

GÉNÈRE 4 SEMAINES PROGRESSIVES (28 objets au total) avec "week_number" de 1 à 4 :
- Semaine 1 (Fondation)  : découverte, volume modéré, séries légères (3x12-15 en salle)
- Semaine 2 (Volume)     : +1 série et +volume pour les séances salle (4x10-12), sports additionnels plus longs
- Semaine 3 (Intensité)  : charges lourdes (4x5-8), moins de reps, sports additionnels en intervalles
- Semaine 4 (Décharge)   : récupération active (2-3x10, charges -20%), sports additionnels légers
→ Les 4 semaines partagent la MÊME structure de jours (mêmes types de séances) mais exercices/séries/intensité progressent.

JSON valide uniquement, 28 objets :
[
  {{"week_number": 1, "day_of_week": 0, "session_name": "Push S1 — Pec/Épaules/Triceps", "session_emoji": "💪",
    "exercises": [{{"name": "Développé couché barre", "sets": "3x12", "rest": "90s"}}]}},
  {{"week_number": 2, "day_of_week": 0, "session_name": "Push S2 — Pec/Épaules/Triceps", "session_emoji": "💪",
    "exercises": [{{"name": "Développé couché barre", "sets": "4x10", "rest": "2min"}}]}},
{sport_day_json_example}  {{"week_number": 1, "day_of_week": 4, "session_name": "REPOS", "session_emoji": "😴", "exercises": []}},
  ...
]"""

    raw = _call(prompt, model=HAIKU, max_tokens=8000)

    try:
        start = raw.find("[")
        if start == -1:
            logging.error("[generate_training_program] no '[' in response: %s", raw[:500])
            raise ValueError("Aucun tableau JSON dans la réponse IA")
        # Trouver le ] de fermeture correspondant (pas forcément le dernier)
        depth = 0
        end = -1
        for i, ch in enumerate(raw[start:], start):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end == -1:
            # Réponse tronquée (max_tokens atteint) — récupérer les objets complets déjà reçus
            logging.warning("[generate_training_program] JSON truncated, attempting recovery…")
            partial = raw[start:]
            # Trouver le dernier objet complet (dernier '}' suivi de whitespace/virgule/fin)
            last_brace = partial.rfind("},")
            if last_brace == -1:
                last_brace = partial.rfind("}")
            if last_brace == -1:
                logging.error("[generate_training_program] unrecoverable truncation, raw[:500]: %s", raw[:500])
                raise ValueError("Tableau JSON non fermé dans la réponse IA")
            recovered = partial[:last_brace + 1] + "]"
            days = json.loads(recovered)
            logging.warning("[generate_training_program] recovered %d day(s) from truncated response", len(days))
        else:
            days = json.loads(raw[start:end])
        # Validation : au moins 28 objets avec week_number et day_of_week
        assert len(days) >= 7
        for d in days:
            assert "day_of_week" in d and "exercises" in d
        # Si l'IA n'a renvoyé qu'une semaine, la répliquer 4 fois avec progression synthétique
        if len(days) < 28:
            days = _expand_to_4_weeks(days, phase_number=phase_number)
        # Garantir week_number sur chaque entrée
        for d in days:
            d.setdefault("week_number", 1)
        return days
    except Exception:
        raise


def _expand_to_4_weeks(week1: list[dict], phase_number: int = 1) -> list[dict]:
    """Génère 4 semaines à partir de la semaine 1 en appliquant une progression simple.
    Le paramètre phase_number ajuste les durées de base des sports additionnels."""
    import re

    # Facteur de durée de base selon la phase (sports)
    phase_base_factors = {1: 1.0, 2: 1.15, 3: 1.25, 4: 1.30}
    base_factor = phase_base_factors.get(min(phase_number, 4), 1.30)

    # Mappings de progression des séries/reps
    def _progress(sets_str: str, week: int) -> str:
        if not sets_str:
            return sets_str
        # Sessions sportives (durées) — on applique le facteur de phase puis la progression hebdo
        if "min" in sets_str.lower():
            try:
                n = int(re.search(r"\d+", sets_str).group())
                # Facteur phase sur la durée de base, puis progression intra-phase
                week_factors = {1: 1.0, 2: 1.15, 3: 1.25, 4: 0.80}
                return f"{round(n * base_factor * week_factors.get(week, 1.0))} min"
            except Exception:
                return sets_str
        # Format "NxM" → ajuster selon la semaine
        m = re.match(r"(\d+)x(\d+)(?:-(\d+))?", sets_str)
        if m:
            sets, rmin, rmax = int(m.group(1)), int(m.group(2)), int(m.group(3) or m.group(2))
            if week == 1:
                return sets_str
            elif week == 2:
                return f"{sets + 1}x{rmin}-{rmax}"
            elif week == 3:
                return f"{sets + 1}x{max(4, rmin - 4)}-{max(6, rmax - 4)}"
            else:   # décharge
                return f"{max(2, sets - 1)}x{rmin}"
        return sets_str

    phase_names = {
        1: "S1 — Fondation",
        2: "S2 — Volume",
        3: "S3 — Intensité",
        4: "S4 — Décharge",
    }

    result = []
    for week in range(1, 5):
        for day in week1:
            d = copy.deepcopy(day)
            d["week_number"] = week
            # Adapter le nom de séance
            base_name = re.sub(r"S\d —? ?[^—]*—?\s*", "", d["session_name"]).strip(" —")
            if not d["session_name"].upper().startswith("REPOS"):
                d["session_name"] = f"{base_name} — {phase_names[week]}"
            for ex in d["exercises"]:
                if ex.get("sets"):
                    ex["sets"] = _progress(ex["sets"], week)
            result.append(d)
    return result


def generate_phase(profile: dict, phase_number: int = 1, total_phases: int = 1, perf_summary: list = None) -> list[dict]:
    """Génère les 4 semaines d'une phase spécifique du programme progressif. Retry une fois si l'IA renvoie du JSON invalide."""
    for attempt in range(2):
        try:
            return generate_training_program(profile, phase_number=phase_number,
                                             total_phases=total_phases, perf_summary=perf_summary)
        except Exception as e:
            if attempt == 0:
                logging.warning("[generate_phase] attempt 1 failed (%s), retrying…", e)
            else:
                raise



def generate_shopping_from_meals(meals_summary: str, is_couple: bool = False) -> str:
    """Génère la liste de courses à partir du résumé des repas de la semaine."""
    if is_couple:
        instructions = "Additionne les quantités des deux profils pour chaque ingrédient (ex: profil 1 : 200g poulet + profil 2 : 160g poulet = 360g poulet au total). Génère une seule liste de courses pour les DEUX personnes ensemble."
    else:
        instructions = "Génère la liste de courses complète groupée par catégorie avec quantités pour 1 personne."

    prompt = f"""Voici les repas planifiés pour la semaine :
{meals_summary[:3000]}

{instructions}
Concis, pas d'intro :
Viandes/Poissons :
- [item] — [quantité totale]
Œufs/Produits laitiers :
Légumes :
Féculents :
Fruits :
Épicerie :"""

    return _call(prompt, model=HAIKU, max_tokens=800)


