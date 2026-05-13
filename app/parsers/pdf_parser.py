import re
from datetime import date, time
from pathlib import Path

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

MUSCLE_KEYWORDS = [
    (["full body", "fullbody", "farmers carry", "farmers march"], "Full Body"),
    # Legs before Arms so "hamstring curl" / "leg curl" match Legs, not Arms
    (["legs", "squat", "glute", "hamstring", "quad", "lunge", "calf", "calves", "hip thrust",
      "step up", "leg press", "leg extension", "leg curl", "hip adduction", "hip abduction",
      "stiff leg", "romanian", "rdl", "leg raise"], "Legs"),
    (["shoulder", "delt", "lateral raise", "face pull", "upright row", "shrug", "landmine"], "Shoulders"),
    (["arms", "bicep", "tricep", "curl", "pushdown", "skullcrusher", "skull", "wrist", "forearm", "hammer curl"], "Arms"),
    (["chest", "pec", "bench press", "chest fly", "chest flye", "cable fly", "cable flye",
      "incline press", "decline press", "incline"], "Chest"),
    # pull up / chin up must be in Back BEFORE the "pull" catch-all below
    (["back", "row", "pulldown", "deadlift", "lat ", "pull-up", "pullup", "pull up",
      "chin-up", "chinup", "chin up", "hyperextension"], "Back"),
    (["core", "ab", "crunch", "plank", "situp", "sit-up", "russian twist", "twist",
      "oblique", "bicycle", "woodchop", "knee raise", "hanging knee"], "Core"),
    (["overhead press"], "Shoulders"),
    (["push"], "Push"),
    (["pull"], "Pull"),
]

_RE_FULL_DATE = re.compile(
    r'(january|february|march|april|may|june|july|august|september|october|november|december)'
    r'\s+(\d{1,2}),?\s+(\d{4})',
    re.IGNORECASE,
)
_RE_PARTIAL_DATE = re.compile(
    r'(january|february|march|april|may|june|july|august|september|october|november|december)'
    r'\s+(\d{1,2}),?',
    re.IGNORECASE,
)
_RE_YEAR = re.compile(r'\b(20\d{2})\b')
_RE_DURATION = re.compile(
    r'(?:(\d+)\s*h\s*[:\s]\s*)?(\d+)\s*m\s*[:\s]\s*(\d+)\s*s',
    re.IGNORECASE,
)
_RE_TOTAL_WEIGHT_HEADER = re.compile(r'total\s+weight', re.IGNORECASE)
_RE_KG_VALUE = re.compile(r'(\d[\d.,]*)\s*kg', re.IGNORECASE)
_RE_WORKOUT_TIME = re.compile(r'(\d{1,2}):(\d{2})\s*(am|pm)', re.IGNORECASE)
_RE_WORKOUT_TITLE = re.compile(r'^My\s+.+$', re.IGNORECASE)

_RE_TOTAL_LINE = re.compile(r'^Total\s+([\d.,]+)\s*(?:kg)?\s*$', re.IGNORECASE)
_RE_SETS_HEADER = re.compile(r'^Sets\s*\((\d+)\)\s*$', re.IGNORECASE)
_RE_SET_ROW = re.compile(
    r'^\s*(\d+)\s+([\d.iIlLoO]+(?:[\s.][\d.iIlLoO]+)?)\s*(?:kg)?\s*[xX×]\s*(\d+)\s*[Rr]eps?\s*$',
    re.IGNORECASE,
)

_SKIP_PATTERNS = [
    re.compile(r'^\d{1,2}:\d{2}$'),
    re.compile(r'^<\s*workout\s+summary', re.IGNORECASE),
    re.compile(r'^[^\w]*$'),            # non-word characters only (icons, arrows)
    re.compile(r'^time\s+total\s+weight', re.IGNORECASE),
    re.compile(r'^time$', re.IGNORECASE),
    re.compile(r'^total\s+weight$', re.IGNORECASE),
    re.compile(r'^\d+\s*m\s*[:\s]\s*\d+\s*s', re.IGNORECASE),
    re.compile(r'^kg$', re.IGNORECASE),
    _RE_FULL_DATE,
]


_VALID_MUSCLE_GROUPS = {
    "Full Body", "Legs", "Shoulders", "Arms", "Chest", "Back", "Core", "Push", "Pull", "Other"
}


def normalize_muscle_group(raw: str | None) -> str | None:
    """Map agent-supplied labels (e.g. 'hamstrings', 'quads') to canonical groups."""
    if not raw:
        return None
    cleaned = raw.strip()
    if cleaned.title() in _VALID_MUSCLE_GROUPS:
        return cleaned.title()
    return infer_muscle_group(cleaned)


def infer_muscle_group(workout_name: str) -> str:
    lower = workout_name.lower()
    for keywords, group in MUSCLE_KEYWORDS:
        if any(kw in lower for kw in keywords):
            return group
    return "Other"


def _clean_weight(token: str) -> float | None:
    token = token.strip()
    token = re.sub(r'\s*[kK][gG]\s*$', '', token).strip()
    token = re.sub(r'(\d)[il](\d)', lambda m: m.group(1) + '1' + m.group(2), token)
    token = re.sub(r'(\d)[il]$', lambda m: m.group(1) + '1', token)
    token = token.replace(',', '.')
    try:
        return float(token)
    except ValueError:
        return None


def _extract_lines(page, y_tolerance: int = 15) -> list[str]:
    """Extract text lines from a page by clustering words by Y proximity."""
    words = page.extract_words(x_tolerance=5, y_tolerance=3)
    if not words:
        return []
    words_sorted = sorted(words, key=lambda w: w["top"])
    groups: list[list] = []
    current = [words_sorted[0]]
    for w in words_sorted[1:]:
        if w["top"] - current[-1]["top"] <= y_tolerance:
            current.append(w)
        else:
            groups.append(current)
            current = [w]
    groups.append(current)
    return [" ".join(w["text"] for w in sorted(g, key=lambda w: w["x0"])) for g in groups]


def _should_skip(line: str) -> bool:
    return any(p.search(line) for p in _SKIP_PATTERNS)


def _parse_exercises(lines: list[str]) -> list[dict]:
    exercises: list[dict] = []
    current_name: str | None = None
    current_total: float | None = None
    current_sets: list[dict] = []
    in_sets = False
    found_date = False

    def _finalize():
        if current_name and current_sets:
            name = re.sub(r'\s+\d*\s*kg\s*$', '', current_name, flags=re.IGNORECASE).strip()
            exercises.append({
                "exercise_name": name,
                "exercise_order": len(exercises),
                "total_volume_kg": current_total,
                "muscle_group": infer_muscle_group(name),
                "sets": list(current_sets),
            })

    for line in lines:
        line = line.strip()
        if not line:
            continue

        if not found_date:
            if _RE_FULL_DATE.search(line):
                found_date = True
            continue

        if _should_skip(line):
            continue

        set_match = _RE_SET_ROW.match(line)
        if set_match:
            in_sets = True
            set_num = int(set_match.group(1))
            weight = _clean_weight(set_match.group(2))
            reps = int(set_match.group(3))
            current_sets.append({"set_number": set_num, "weight_kg": weight, "reps": reps})
            continue

        total_match = _RE_TOTAL_LINE.match(line)
        if total_match:
            current_total = float(total_match.group(1).replace(",", "."))
            continue

        sets_match = _RE_SETS_HEADER.match(line)
        if sets_match:
            in_sets = True
            continue

        # Bare "X kg ." partial line at page break — skip
        if _RE_KG_VALUE.match(line) and len(line.split()) <= 3:
            continue

        # Anything else after collecting sets = new exercise
        if current_name is not None and (in_sets or current_total is not None):
            _finalize()
            current_name = None
            current_total = None
            current_sets = []
            in_sets = False

        # Candidate exercise name (length ≥ 3, not starting with digit, not the workout title)
        if len(line) >= 3 and not line[0].isdigit() and not _RE_WORKOUT_TITLE.match(line):
            if current_name is not None and not in_sets and current_total is None:
                # Multi-line name — append continuation (e.g. "Barbell Hip" + "Thrust")
                current_name = current_name + " " + line
            else:
                current_name = line

    _finalize()
    return exercises


def parse_pdf(pdf_path: str | Path) -> dict | None:
    if pdfplumber is None:
        raise ImportError("pdfplumber is required: pip install pdfplumber")

    pdf_path = Path(pdf_path)
    all_lines: list[str] = []
    last_page_lines: list[str] = []

    with pdfplumber.open(pdf_path) as pdf:
        if not pdf.pages:
            return None
        for page in pdf.pages:
            all_lines.extend(_extract_lines(page))
        last_page_lines = _extract_lines(pdf.pages[-1])

    last_page_text = "\n".join(last_page_lines)

    # --- Workout name from last page ---
    # Handle both single-line "My X Workout" and two-line "My X\nWorkout"
    workout_name = None
    for i, line in enumerate(last_page_lines):
        if _RE_WORKOUT_TITLE.match(line):
            candidate = line.strip()
            # If title doesn't end with "Workout", check next line
            if not re.search(r'\bWorkout\b', candidate, re.IGNORECASE):
                if i + 1 < len(last_page_lines):
                    next_line = last_page_lines[i + 1].strip()
                    if re.match(r'^Workout$', next_line, re.IGNORECASE):
                        candidate = candidate + " " + next_line
            workout_name = candidate
            break

    if not workout_name:
        # Fallback: search all pages
        for i, line in enumerate(all_lines):
            if _RE_WORKOUT_TITLE.match(line):
                candidate = line.strip()
                if not re.search(r'\bWorkout\b', candidate, re.IGNORECASE):
                    if i + 1 < len(all_lines):
                        nxt = all_lines[i + 1].strip()
                        if re.match(r'^Workout$', nxt, re.IGNORECASE):
                            candidate = candidate + " " + nxt
                workout_name = candidate
                break

    if not workout_name:
        workout_name = pdf_path.stem

    # --- Date ---
    workout_date = None
    full_text = "\n".join(all_lines)
    date_match = _RE_FULL_DATE.search(full_text)
    if date_match:
        month = MONTH_MAP[date_match.group(1).lower()]
        day = int(date_match.group(2))
        year = int(date_match.group(3))
        workout_date = date(year, month, day)
    else:
        # Fallback: month+day on one line, year anywhere in document
        partial = _RE_PARTIAL_DATE.search(full_text)
        year_match = _RE_YEAR.search(full_text)
        if partial and year_match:
            month = MONTH_MAP[partial.group(1).lower()]
            day = int(partial.group(2))
            year = int(year_match.group(1))
            workout_date = date(year, month, day)

    # --- Workout time ---
    workout_time = None
    time_match = _RE_WORKOUT_TIME.search(last_page_text)
    if time_match:
        hour = int(time_match.group(1))
        minute = int(time_match.group(2))
        ampm = time_match.group(3).lower()
        if ampm == "pm" and hour != 12:
            hour += 12
        elif ampm == "am" and hour == 12:
            hour = 0
        workout_time = time(hour, minute)

    # --- Duration ---
    duration_seconds = None
    dur_match = _RE_DURATION.search(last_page_text)
    if dur_match:
        hours = int(dur_match.group(1) or 0)
        minutes = int(dur_match.group(2))
        seconds = int(dur_match.group(3))
        duration_seconds = hours * 3600 + minutes * 60 + seconds

    # --- Total volume from last page ---
    total_volume_kg = None
    tw_pos = _RE_TOTAL_WEIGHT_HEADER.search(last_page_text)
    if tw_pos:
        after = last_page_text[tw_pos.end():]
        kg_match = _RE_KG_VALUE.search(after[:80])
        if kg_match:
            total_volume_kg = float(kg_match.group(1).replace(",", "."))

    # --- Exercises ---
    exercises = _parse_exercises(all_lines)

    if workout_date is None:
        return None

    return {
        "workout_name": workout_name,
        "workout_date": workout_date,
        "workout_time": workout_time,
        "duration_seconds": duration_seconds,
        "total_volume_kg": total_volume_kg,
        "muscle_group": infer_muscle_group(workout_name),
        "source_file": pdf_path.name,
        "exercises": exercises,
    }
