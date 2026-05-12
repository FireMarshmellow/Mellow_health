"""
Local dev runner — no Docker required.
Uses SQLite (stored as mellow_health.db in the project root).

Usage:
    py run_local.py
    python run_local.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
APP_DIR = os.path.join(ROOT, "app")

os.environ.setdefault("DATABASE_URL", f"sqlite:///{ROOT}/mellow_health.db")
os.environ.setdefault("DATA_ZIP_PATH", os.path.join(ROOT, "Raw data", "Strava_data_export.zip"))

sys.path.insert(0, APP_DIR)

if __name__ == "__main__":
    from importer import run_import
    run_import()

    # chdir so that relative paths ("static", "templates") resolve correctly
    # in both the main process and reload worker subprocesses
    os.chdir(APP_DIR)

    import uvicorn
    uvicorn.run(
        "main:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[APP_DIR],
    )
