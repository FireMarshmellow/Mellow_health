import csv
import io
import os
import zipfile
from datetime import datetime

from sqlalchemy import text

from database import SessionLocal, engine, Base
from models import Activity
from parsers.gpx_parser import parse_gpx
from parsers.fit_parser import parse_fit_gz
from parsers.apple_parser import parse_apple_health

STRAVA_ZIP = os.environ.get("DATA_ZIP_PATH", "/data/Strava_data_export.zip")
APPLE_ZIP  = os.path.join(os.path.dirname(STRAVA_ZIP), "Apple_health_export.zip")

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

    _import_strava()
    _import_apple_health()


# ── Schema migration ──────────────────────────────────────────────────────────

def _migrate_schema():
    """Add columns introduced after initial release without dropping the table."""
    with engine.connect() as conn:
        # Add `source` column if missing
        try:
            conn.execute(text("SELECT source FROM activities LIMIT 1"))
        except Exception:
            conn.execute(text(
                "ALTER TABLE activities ADD COLUMN source VARCHAR(50) DEFAULT 'strava'"
            ))
            conn.commit()
            print("[migrate] Added `source` column.")


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
