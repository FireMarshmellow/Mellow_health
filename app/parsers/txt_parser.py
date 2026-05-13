import re
from datetime import date, time
from pathlib import Path

try:
    from parsers.pdf_parser import infer_muscle_group
except ImportError:
    from pdf_parser import infer_muscle_group

MONTH_MAP = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}

_RE_DATE = re.compile(
    r'^(\d{1,2})\s+(january|february|march|april|may|june|july|august|'
    r'september|october|november|december)\s+(\d{4})(?:,\s*(\d{1,2}):(\d{2}))?',
    re.IGNORECASE,
)
_RE_WEIGHTED_SET = re.compile(r'^(\d+)\s*[xX×]\s*([\d.]+)\s*kg', re.IGNORECASE)
_RE_REPS_ONLY = re.compile(r'^(\d+)\s+reps?\b', re.IGNORECASE)
# NxN with no kg suffix — either "3x5 assisted" (BW multi-set) or "12x22" (reps×weight)
_RE_BW_MULTI = re.compile(r'^(\d+)\s*[xX×]\s*(\d+)\b(?!\s*\.?\s*\d*\s*kg)', re.IGNORECASE)
# Time-based lines: "1m", "2m", "1m 30s", "1m : 30s"
_RE_TIME_LINE = re.compile(r'^\d+[mM]\s*(?:[:\s.]+\s*\d+[sS])?\s*$')
_RE_EM_DASH = re.compile(r'\s[—–]\s|\s--\s')
_RE_STATS_DUR = re.compile(r'(?:(\d+)h\s*)?(\d+)m(?:\s*[:\s.]+\s*(\d+)s)?', re.IGNORECASE)
_RE_STATS_VOL = re.compile(r'([\d.,]+)\s*kg', re.IGNORECASE)


def _parse_stats(stats: str) -> tuple[int | None, float | None]:
    dur_m = _RE_STATS_DUR.search(stats)
    duration = None
    if dur_m:
        h = int(dur_m.group(1) or 0)
        m = int(dur_m.group(2))
        s = int(dur_m.group(3) or 0)
        duration = h * 3600 + m * 60 + s
    vol_m = _RE_STATS_VOL.search(stats)
    volume = float(vol_m.group(1).replace(",", ".")) if vol_m else None
    return duration, volume


def _split_header(line: str) -> tuple[str, int | None, float | None]:
    """Split 'Name — stats' into (name, duration_secs, volume_kg)."""
    m = _RE_EM_DASH.search(line)
    if m:
        name = line[:m.start()].strip()
        stats = line[m.end():]
        if stats and stats[0].isdigit():
            dur, vol = _parse_stats(stats)
            return name, dur, vol
        # "Wednesday — Legs": after the dash isn't numeric, keep full line as name
        return line, None, None
    return line, None, None


def _is_header_line(line: str) -> bool:
    if line.startswith("My ") or line.lower().startswith("morning workout"):
        return True
    if line.startswith("("):
        return True
    low = line.lower()
    if low.endswith("workout") or low.endswith("workout)"):
        return True
    if _RE_EM_DASH.search(line):
        return True
    return False


def _parse_set(line: str, set_number: int) -> list[dict]:
    """Try to parse line as one or more set records. Returns [] if not a set line."""
    stripped = line.strip()
    if not stripped or ":" in stripped:
        return []
    if _RE_TIME_LINE.match(stripped):
        return []

    # Weighted: NxNkg
    m = _RE_WEIGHTED_SET.match(stripped)
    if m:
        return [{"set_number": set_number, "weight_kg": float(m.group(2)), "reps": int(m.group(1))}]

    # Bodyweight reps: "N reps"
    m = _RE_REPS_ONLY.match(stripped)
    if m:
        return [{"set_number": set_number, "weight_kg": None, "reps": int(m.group(1))}]

    # NxN without kg
    m = _RE_BW_MULTI.match(stripped)
    if m:
        first, second = int(m.group(1)), int(m.group(2))
        if second >= 10:
            # e.g. "12x22" for machine — treat as reps × weight_kg
            return [{"set_number": set_number, "weight_kg": float(second), "reps": first}]
        else:
            # e.g. "3x5 assisted" — expand to N sets of M reps bodyweight
            return [
                {"set_number": set_number + i, "weight_kg": None, "reps": second}
                for i in range(first)
            ]

    return []


def _parse_exercises_txt(lines: list[str]) -> list[dict]:
    exercises: list[dict] = []
    current_name: str | None = None
    current_sets: list[dict] = []

    def _finalize():
        if current_name and current_sets:
            vol = sum(
                s["weight_kg"] * s["reps"] for s in current_sets if s["weight_kg"]
            )
            exercises.append({
                "exercise_name": current_name,
                "exercise_order": len(exercises),
                "total_volume_kg": round(vol, 1) if vol > 0 else None,
                "muscle_group": infer_muscle_group(current_name),
                "sets": list(current_sets),
            })

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if ":" in stripped:
            continue
        if _RE_TIME_LINE.match(stripped):
            continue

        # Try to parse as a set (only if we have a current exercise)
        if current_name is not None:
            set_num = len(current_sets) + 1
            sets = _parse_set(stripped, set_num)
            if sets:
                # Fix set_numbers for multi-set expansions
                for i, s in enumerate(sets):
                    s["set_number"] = len(current_sets) + 1 + i
                current_sets.extend(sets)
                continue

        # Lines starting with a digit that aren't sets or reps are noise — skip
        if stripped[0].isdigit():
            continue

        # New exercise name
        _finalize()
        current_name = stripped
        current_sets = []

    _finalize()
    return exercises


def _parse_session(date_m: re.Match, lines: list[str]) -> dict | None:
    day = int(date_m.group(1))
    month = MONTH_MAP[date_m.group(2).lower()]
    year = int(date_m.group(3))
    hour = int(date_m.group(4)) if date_m.group(4) else None
    minute = int(date_m.group(5)) if date_m.group(5) else None

    workout_date = date(year, month, day)
    workout_time = time(hour, minute) if hour is not None else None

    workout_name: str | None = None
    duration_seconds: int | None = None
    total_volume_kg: float | None = None
    exercise_start = 0

    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if _is_header_line(stripped):
            workout_name, duration_seconds, total_volume_kg = _split_header(stripped)
            exercise_start = i + 1
        else:
            exercise_start = i
        break

    if workout_name is None:
        workout_name = "Workout"

    exercises = _parse_exercises_txt(lines[exercise_start:])
    if not exercises:
        return None

    if total_volume_kg is None:
        vols = [ex["total_volume_kg"] for ex in exercises if ex["total_volume_kg"]]
        total_volume_kg = round(sum(vols), 1) if vols else None

    return {
        "workout_name": workout_name,
        "workout_date": workout_date,
        "workout_time": workout_time,
        "duration_seconds": duration_seconds,
        "total_volume_kg": total_volume_kg,
        "muscle_group": infer_muscle_group(workout_name),
        "source_file": "Workout_log.txt",
        "exercises": exercises,
    }


def parse_workout_log(file_path: str | Path) -> list[dict]:
    """Parse Workout_log.txt into a list of workout dicts."""
    text = Path(file_path).read_text(encoding="utf-8")
    lines = text.splitlines()

    sessions: list[tuple] = []
    current_match: re.Match | None = None
    current_lines: list[str] = []

    for line in lines:
        m = _RE_DATE.match(line.strip())
        if m:
            if current_match is not None:
                sessions.append((current_match, current_lines))
            current_match = m
            current_lines = []
        else:
            if current_match is not None:
                current_lines.append(line)
    if current_match is not None:
        sessions.append((current_match, current_lines))

    workouts = []
    for date_m, session_lines in sessions:
        w = _parse_session(date_m, session_lines)
        if w:
            workouts.append(w)
    return workouts
