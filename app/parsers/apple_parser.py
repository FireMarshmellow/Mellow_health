import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime, timezone

from parsers.gpx_parser import parse_gpx

APPLE_DATE_FMT = "%Y-%m-%d %H:%M:%S %z"

# Strip this prefix from workoutActivityType values
HK_PREFIX = "HKWorkoutActivityType"

# Map HK type → display name
SPORT_MAP = {
    "Running":                       "Run",
    "Cycling":                       "Ride",
    "Walking":                       "Walk",
    "Hiking":                        "Hike",
    "HighIntensityIntervalTraining": "HIIT",
    "Yoga":                          "Yoga",
    "Swimming":                      "Swim",
    "Elliptical":                    "Elliptical",
    "StairClimbing":                 "StairClimb",
    "CrossTraining":                 "CrossTrain",
}


def parse_apple_health(zip_path: str) -> list[dict]:
    with zipfile.ZipFile(zip_path) as zf:
        namelist = set(zf.namelist())

        print("[apple] Parsing export.xml…")
        with zf.open("apple_health_export/export.xml") as f:
            tree = ET.parse(f)
        root = tree.getroot()

        workouts = root.findall("Workout")
        print(f"[apple] Found {len(workouts)} workout records.")

        activities = []
        for workout in workouts:
            entry = _parse_workout(workout, zf, namelist)
            if entry:
                activities.append(entry)

    return activities


def _parse_workout(elem, zf: zipfile.ZipFile, namelist: set) -> dict | None:
    raw_type = elem.get("workoutActivityType", "").replace(HK_PREFIX, "")
    sport_type = SPORT_MAP.get(raw_type, raw_type)

    start_date = _parse_date(elem.get("startDate", ""))
    if start_date is None:
        return None

    duration_min = _safe_float(elem.get("duration")) or 0.0
    duration_sec = int(duration_min * 60)

    distance_km = None
    calories = None
    avg_hr = None
    max_hr = None
    elevation_gain = None

    for child in elem:
        tag = child.tag

        if tag == "WorkoutStatistics":
            stat_type = child.get("type", "")
            if "Distance" in stat_type:
                val = _safe_float(child.get("sum"))
                unit = child.get("unit", "km")
                if val is not None:
                    distance_km = val * 1.60934 if unit == "mi" else val
            elif "ActiveEnergyBurned" in stat_type:
                val = _safe_float(child.get("sum"))
                if val is not None:
                    calories = int(val)
            elif "HeartRate" in stat_type:
                avg = _safe_float(child.get("average"))
                mx = _safe_float(child.get("maximum"))
                if avg is not None:
                    avg_hr = avg
                if mx is not None:
                    max_hr = int(mx)

        elif tag == "MetadataEntry" and child.get("key") == "HKElevationAscended":
            raw = child.get("value", "")  # e.g. "1389 cm"
            parts = raw.split()
            if len(parts) == 2:
                val = _safe_float(parts[0])
                unit = parts[1]
                if val is not None:
                    elevation_gain = val / 100.0 if unit == "cm" else val  # cm → m

    distance_meters = distance_km * 1000 if distance_km else None

    avg_pace = None
    avg_speed = None
    if distance_km and duration_min > 0:
        hours = duration_min / 60.0
        avg_speed = round(distance_km / hours, 2)
    if distance_km and duration_sec > 0:
        avg_pace = duration_sec / distance_km

    # GPS — look for WorkoutRoute > FileReference
    has_gps = False
    gps_track = None
    for child in elem:
        if child.tag == "WorkoutRoute":
            for sub in child:
                if sub.tag == "FileReference":
                    rel_path = sub.get("path", "")
                    zip_entry = "apple_health_export" + rel_path
                    if zip_entry in namelist:
                        try:
                            file_bytes = zf.read(zip_entry)
                            parsed = parse_gpx(file_bytes)
                            has_gps = parsed["has_gps"]
                            gps_track = parsed["gps_track"]
                        except Exception as e:
                            print(f"[apple] GPX error {rel_path}: {e}")

    # Use epoch-milliseconds as the unique source ID (won't collide with Strava IDs)
    source_id = int(start_date.timestamp() * 1000)

    return {
        "source_id": source_id,
        "source": "apple_health",
        "name": f"{sport_type}",
        "sport_type": sport_type,
        "start_date": start_date.replace(tzinfo=None),  # store as naive UTC
        "distance_meters": distance_meters,
        "duration_seconds": duration_sec,
        "moving_time_seconds": duration_sec,
        "elevation_gain": elevation_gain,
        "avg_pace_sec_per_km": avg_pace,
        "avg_speed_kmh": avg_speed,
        "avg_heart_rate": avg_hr,
        "max_heart_rate": max_hr,
        "calories": calories,
        "has_gps": has_gps,
        "gps_track": gps_track,
        "source_file": "Apple_health_export.zip",
    }


def _parse_date(s: str) -> datetime | None:
    try:
        return datetime.strptime(s.strip(), APPLE_DATE_FMT)
    except (ValueError, AttributeError):
        return None


def _safe_float(val) -> float | None:
    try:
        s = str(val).strip()
        return float(s) if s else None
    except (ValueError, TypeError):
        return None
