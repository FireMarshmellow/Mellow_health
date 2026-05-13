"""
Run once to migrate data from a SQLite file into the PostgreSQL database.

Usage (inside the web container via Portainer console):
    python migrate_sqlite_to_pg.py /path/to/health.db

DATABASE_URL is read from the environment (already set by docker-compose).
"""

import sqlite3
import sys
import os
from sqlalchemy import create_engine, text

SQLITE_PATH = sys.argv[1] if len(sys.argv) > 1 else "/data/health.db"
PG_URL = os.environ["DATABASE_URL"]

sqlite = sqlite3.connect(SQLITE_PATH)
sqlite.row_factory = sqlite3.Row
pg = create_engine(PG_URL)

TABLES = [
    "workout_plans",
    "workout_plan_days",
    "activities",
    "strength_workouts",
    "strength_exercises",
    "strength_sets",
]

# Sequences to reset after bulk insert so auto-increment stays correct
SEQUENCES = {
    "workout_plans":     "workout_plans_id_seq",
    "workout_plan_days": "workout_plan_days_id_seq",
    "activities":        "activities_id_seq",
    "strength_workouts": "strength_workouts_id_seq",
    "strength_exercises":"strength_exercises_id_seq",
    "strength_sets":     "strength_sets_id_seq",
}

with pg.begin() as conn:
    for table in TABLES:
        rows = sqlite.execute(f"SELECT * FROM {table}").fetchall()
        if not rows:
            print(f"  {table}: empty, skipping")
            continue

        cols = rows[0].keys()
        placeholders = ", ".join(f":{c}" for c in cols)
        col_list = ", ".join(cols)
        sql = text(
            f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
            f"ON CONFLICT DO NOTHING"
        )
        data = [dict(r) for r in rows]
        conn.execute(sql, data)
        print(f"  {table}: {len(data)} rows copied")

    # Reset sequences so new inserts don't collide with migrated IDs
    for table, seq in SEQUENCES.items():
        conn.execute(text(
            f"SELECT setval('{seq}', COALESCE((SELECT MAX(id) FROM {table}), 1))"
        ))

print("Migration complete.")
