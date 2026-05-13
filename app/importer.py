import csv
import io
import os
import zipfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

from database import SessionLocal, engine, Base
from models import Activity, StrengthWorkout, StrengthExercise, StrengthSet
from parsers.gpx_parser import parse_gpx
from parsers.fit_parser import parse_fit_gz
from parsers.apple_parser import parse_apple_health
from parsers.pdf_parser import parse_pdf
from parsers.txt_parser import parse_workout_log

STRAVA_ZIP = os.environ.get("DATA_ZIP_PATH", "/data/Strava_data_export.zip")
APPLE_ZIP  = os.path.join(os.path.dirname(STRAVA_ZIP), "Apple_health_export.zip")
WORKOUT_LOG = os.path.join(os.path.dirname(STRAVA_ZIP), "Workout_log.txt")

CSV_DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"

COL_STRAVA_ID   = 0
COL_DATE        = 1
COL_NAME        = 2
COL_SPORT       = 3
COL_ELAPSED     = 5
COL_FILENAME    = 12
COL_MOVING_TIME = 16
COL_DISTANCE_M  = 17
COL_AVG_SPEED   = 19
COL_ELEVATION   = 20
COL_MAX_HR      = 30
COL_AVG_HR      = 31
COL_CALORIES    = 34


def run_import():
    Base.metadata.create_all(bind=engine)
    _migrate_schema()

    if _db_has_data():
        print("[import] Database already populated, skipping import.")
        return

    _import_strava()
    _import_apple_health()
    _import_strength_training()
    _import_workout_log()


def _db_has_data() -> bool:
    try:
        with engine.connect() as conn:
            if conn.execute(text("SELECT 1 FROM activities LIMIT 1")).fetchone():
                return True
            if conn.execute(text("SELECT 1 FROM strength_workouts LIMIT 1")).fetchone():
                return True
    except Exception:
        pass
    return False


# ── Schema migration ──────────────────────────────────────────────────────────

def _migrate_schema():
    """Add columns introduced after initial release without dropping the table."""
    with engine.connect() as conn:
        # Add `source` column to activities if missing
        try:
            conn.execute(text("SELECT source FROM activities LIMIT 1"))
        except Exception:
            conn.execute(text(
                "ALTER TABLE activities ADD COLUMN source VARCHAR(50) DEFAULT 'strava'"
            ))
            conn.commit()
            print("[migrate] Added `source` column.")

        # Add `muscle_group` to strength_exercises if missing
        try:
            conn.execute(text("SELECT muscle_group FROM strength_exercises LIMIT 1"))
        except Exception:
            conn.execute(text(
                "ALTER TABLE strength_exercises ADD COLUMN muscle_group VARCHAR(100)"
            ))
            conn.commit()
            print("[migrate] Added `muscle_group` column to strength_exercises.")
            # Populate existing rows
            _backfill_exercise_muscle_groups()

        # Add `plan_workout_id` to strength_workouts if missing
        try:
            conn.execute(text("SELECT plan_workout_id FROM strength_workouts LIMIT 1"))
        except Exception:
            pass  # handled below in separate connection to avoid PG transaction-abort

    # Separate connection for ALTER TABLE so a failed SELECT above doesn't abort the transaction
    try:
        with engine.connect() as _c:
            _c.execute(text("SELECT plan_workout_id FROM strength_workouts LIMIT 1"))
    except Exception:
        with engine.connect() as _c:
            _c.execute(text("ALTER TABLE strength_workouts ADD COLUMN plan_workout_id INTEGER"))
            _c.commit()
            print("[migrate] Added `plan_workout_id` column to strength_workouts.")


# ── Strava ────────────────────────────────────────────────────────────────────

def _import_strava():
    if not os.path.exists(STRAVA_ZIP):
        print(f"[strava] ZIP not found at {STRAVA_ZIP}, skipping.")
        return

    db = SessionLocal()
    try:
        with zipfile.ZipFile(STRAVA_ZIP, "r") as zf:
            namelist = zf.namelist()

            csv_path = next((n for n in namelist if n.endswith("activities.csv")), None)
            if not csv_path:
                print("[strava] activities.csv not found in ZIP.")
                return

            with zf.open(csv_path) as f:
                content = f.read().decode("utf-8-sig")

            rows = list(csv.reader(io.StringIO(content)))[1:]
            print(f"[strava] Found {len(rows)} activities.")

            imported = skipped = errors = 0
            for row in rows:
                if not row or not row[COL_STRAVA_ID].strip():
                    continue
                strava_id = _safe_int(row[COL_STRAVA_ID])
                if strava_id is None:
                    continue
                if db.query(Activity).filter_by(strava_id=strava_id).first():
                    skipped += 1
                    continue
                try:
                    activity = _build_strava_activity(row, zf, namelist)
                    db.add(activity)
                    db.commit()
                    imported += 1
                except Exception as e:
                    db.rollback()
                    errors += 1
                    print(f"[strava] Error on {strava_id}: {e}")

        print(f"[strava] Done. imported={imported}, skipped={skipped}, errors={errors}")
    finally:
        db.close()


def _build_strava_activity(row: list, zf: zipfile.ZipFile, namelist: list) -> Activity:
    strava_id = int(row[COL_STRAVA_ID].strip())
    name = row[COL_NAME].strip() or "Untitled"
    sport_type = row[COL_SPORT].strip()

    start_date = None
    raw_date = row[COL_DATE].strip()
    if raw_date:
        try:
            start_date = datetime.strptime(raw_date, CSV_DATE_FORMAT)
        except ValueError:
            pass

    distance_meters = _safe_float(row[COL_DISTANCE_M])
    elapsed_seconds = _safe_int(row[COL_ELAPSED])
    moving_time_seconds = _safe_int(row[COL_MOVING_TIME])
    elevation_gain = _safe_float(row[COL_ELEVATION])
    avg_speed_ms = _safe_float(row[COL_AVG_SPEED])
    avg_speed_kmh = round(avg_speed_ms * 3.6, 2) if avg_speed_ms else None
    avg_heart_rate = _safe_float(row[COL_AVG_HR])
    max_heart_rate = _safe_int(row[COL_MAX_HR])
    calories = _safe_int(row[COL_CALORIES])

    avg_pace = None
    if moving_time_seconds and distance_meters and distance_meters > 0:
        avg_pace = moving_time_seconds / (distance_meters / 1000.0)

    has_gps = False
    gps_track = None
    filename = row[COL_FILENAME].strip() if len(row) > COL_FILENAME else ""
    if filename:
        basename = filename.split("/")[-1]
        zip_entry = next(
            (n for n in namelist if n.endswith("/" + basename) or n == basename), None
        )
        if zip_entry:
            file_bytes = zf.read(zip_entry)
            try:
                if filename.endswith(".gpx"):
                    parsed = parse_gpx(file_bytes)
                elif filename.endswith(".fit.gz"):
                    parsed = parse_fit_gz(file_bytes)
                else:
                    parsed = {"has_gps": False, "gps_track": None}
                has_gps = parsed["has_gps"]
                gps_track = parsed["gps_track"]
            except Exception as e:
                print(f"[strava] GPS parse error for {basename}: {e}")

    return Activity(
        strava_id=strava_id,
        source="strava",
        name=name,
        sport_type=sport_type,
        start_date=start_date,
        distance_meters=distance_meters,
        duration_seconds=elapsed_seconds,
        moving_time_seconds=moving_time_seconds,
        elevation_gain=elevation_gain,
        avg_pace_sec_per_km=avg_pace,
        avg_speed_kmh=avg_speed_kmh,
        avg_heart_rate=avg_heart_rate,
        max_heart_rate=max_heart_rate,
        calories=calories,
        has_gps=has_gps,
        gps_track=gps_track,
        source_file=filename,
    )


# ── Apple Health ──────────────────────────────────────────────────────────────

def _import_apple_health():
    if not os.path.exists(APPLE_ZIP):
        print(f"[apple] ZIP not found at {APPLE_ZIP}, skipping.")
        return

    activities = parse_apple_health(APPLE_ZIP)

    db = SessionLocal()
    try:
        imported = skipped = errors = 0
        for entry in activities:
            source_id = entry["source_id"]
            if db.query(Activity).filter_by(strava_id=source_id).first():
                skipped += 1
                continue
            try:
                activity = Activity(
                    strava_id=source_id,
                    source=entry["source"],
                    name=entry["name"],
                    sport_type=entry["sport_type"],
                    start_date=entry["start_date"],
                    distance_meters=entry["distance_meters"],
                    duration_seconds=entry["duration_seconds"],
                    moving_time_seconds=entry["moving_time_seconds"],
                    elevation_gain=entry["elevation_gain"],
                    avg_pace_sec_per_km=entry["avg_pace_sec_per_km"],
                    avg_speed_kmh=entry["avg_speed_kmh"],
                    avg_heart_rate=entry["avg_heart_rate"],
                    max_heart_rate=entry["max_heart_rate"],
                    calories=entry["calories"],
                    has_gps=entry["has_gps"],
                    gps_track=entry["gps_track"],
                    source_file=entry["source_file"],
                )
                db.add(activity)
                db.commit()
                imported += 1
            except Exception as e:
                db.rollback()
                errors += 1
                print(f"[apple] Error on source_id={source_id}: {e}")

        print(f"[apple] Done. imported={imported}, skipped={skipped}, errors={errors}")
    finally:
        db.close()


def _backfill_exercise_muscle_groups():
    from parsers.pdf_parser import infer_muscle_group
    db = SessionLocal()
    try:
        exercises = db.query(StrengthExercise).all()
        for ex in exercises:
            ex.muscle_group = infer_muscle_group(ex.exercise_name)
        db.commit()
        print(f"[migrate] Populated muscle_group for {len(exercises)} exercises.")
    finally:
        db.close()


# ── Strength Training ─────────────────────────────────────────────────────────

def _import_strength_training():
    data_dir = Path(os.path.dirname(STRAVA_ZIP))
    musalwiki_dir = data_dir / "musalwiki"

    # Also handle a zipped version
    musalwiki_zip = data_dir / "musalwiki.zip"
    if not musalwiki_dir.exists() and musalwiki_zip.exists():
        import tempfile, shutil
        tmp = Path(tempfile.mkdtemp())
        with zipfile.ZipFile(musalwiki_zip) as zf:
            zf.extractall(tmp)
        extracted = list(tmp.rglob("*.pdf"))
        if extracted:
            musalwiki_dir = extracted[0].parent
        else:
            print("[strength] No PDFs found in musalwiki.zip, skipping.")
            return

    if not musalwiki_dir.exists():
        print(f"[strength] Musalwiki directory not found at {musalwiki_dir}, skipping.")
        return

    pdf_files = sorted(musalwiki_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"[strength] No PDFs found in {musalwiki_dir}, skipping.")
        return

    print(f"[strength] Found {len(pdf_files)} PDFs.")

    db = SessionLocal()
    try:
        imported = skipped = errors = 0
        for pdf_path in pdf_files:
            try:
                data = parse_pdf(pdf_path)
                if data is None:
                    print(f"[strength] Skipping {pdf_path.name}: could not parse date.")
                    errors += 1
                    continue

                existing = (
                    db.query(StrengthWorkout)
                    .filter_by(workout_date=data["workout_date"], workout_name=data["workout_name"])
                    .first()
                )
                if existing:
                    skipped += 1
                    continue

                workout = StrengthWorkout(
                    workout_name=data["workout_name"],
                    workout_date=data["workout_date"],
                    workout_time=data["workout_time"],
                    duration_seconds=data["duration_seconds"],
                    total_volume_kg=data["total_volume_kg"],
                    muscle_group=data["muscle_group"],
                    source_file=data["source_file"],
                )
                db.add(workout)
                db.flush()  # get workout.id

                for ex_data in data["exercises"]:
                    exercise = StrengthExercise(
                        workout_id=workout.id,
                        exercise_name=ex_data["exercise_name"],
                        exercise_order=ex_data["exercise_order"],
                        total_volume_kg=ex_data["total_volume_kg"],
                        muscle_group=ex_data["muscle_group"],
                    )
                    db.add(exercise)
                    db.flush()

                    for s in ex_data["sets"]:
                        db.add(StrengthSet(
                            exercise_id=exercise.id,
                            set_number=s["set_number"],
                            weight_kg=s["weight_kg"],
                            reps=s["reps"],
                        ))

                db.commit()
                imported += 1

            except Exception as e:
                db.rollback()
                errors += 1
                print(f"[strength] Error on {pdf_path.name}: {e}")

        print(f"[strength] Done. imported={imported}, skipped={skipped}, errors={errors}")
    finally:
        db.close()


# ── Workout Log (text) ────────────────────────────────────────────────────────

def _import_workout_log():
    if not os.path.exists(WORKOUT_LOG):
        print(f"[log] Workout_log.txt not found at {WORKOUT_LOG}, skipping.")
        return

    try:
        workouts = parse_workout_log(WORKOUT_LOG)
    except Exception as e:
        print(f"[log] Failed to parse {WORKOUT_LOG}: {e}")
        return

    print(f"[log] Parsed {len(workouts)} sessions from Workout_log.txt.")

    db = SessionLocal()
    try:
        imported = skipped = errors = 0
        for data in workouts:
            # For generic-named sessions, skip if ANY workout already exists on that date
            # (prevents duplicating PDF-imported sessions that had no formal name in the log)
            if data["workout_name"] == "Workout":
                if db.query(StrengthWorkout).filter_by(workout_date=data["workout_date"]).first():
                    skipped += 1
                    continue

            existing = (
                db.query(StrengthWorkout)
                .filter_by(workout_date=data["workout_date"], workout_name=data["workout_name"])
                .first()
            )
            if existing:
                skipped += 1
                continue
            try:
                workout = StrengthWorkout(
                    workout_name=data["workout_name"],
                    workout_date=data["workout_date"],
                    workout_time=data["workout_time"],
                    duration_seconds=data["duration_seconds"],
                    total_volume_kg=data["total_volume_kg"],
                    muscle_group=data["muscle_group"],
                    source_file=data["source_file"],
                )
                db.add(workout)
                db.flush()

                for ex_data in data["exercises"]:
                    exercise = StrengthExercise(
                        workout_id=workout.id,
                        exercise_name=ex_data["exercise_name"],
                        exercise_order=ex_data["exercise_order"],
                        total_volume_kg=ex_data["total_volume_kg"],
                        muscle_group=ex_data["muscle_group"],
                    )
                    db.add(exercise)
                    db.flush()

                    for s in ex_data["sets"]:
                        db.add(StrengthSet(
                            exercise_id=exercise.id,
                            set_number=s["set_number"],
                            weight_kg=s["weight_kg"],
                            reps=s["reps"],
                        ))

                db.commit()
                imported += 1
            except Exception as e:
                db.rollback()
                errors += 1
                print(f"[log] Error on {data['workout_date']} '{data['workout_name']}': {e}")

        print(f"[log] Done. imported={imported}, skipped={skipped}, errors={errors}")
    finally:
        db.close()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_int(val) -> int | None:
    try:
        s = str(val).strip()
        return int(float(s)) if s else None
    except (ValueError, TypeError):
        return None


def _safe_float(val) -> float | None:
    try:
        s = str(val).strip()
        return float(s) if s else None
    except (ValueError, TypeError):
        return None


if __name__ == "__main__":
    run_import()
