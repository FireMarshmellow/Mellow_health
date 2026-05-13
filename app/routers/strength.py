import json
from collections import defaultdict
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from database import get_db
from models import StrengthWorkout, StrengthExercise, StrengthSet

router = APIRouter(prefix="/strength")
templates = Jinja2Templates(directory="templates")


def _fmt_duration(seconds: int | None) -> str:
    if not seconds:
        return "—"
    m, s = divmod(seconds, 60)
    return f"{m}m {s:02d}s"


@router.get("/summary", response_class=HTMLResponse)
def strength_summary(request: Request, db: Session = Depends(get_db)):
    agg = db.query(
        func.count(StrengthWorkout.id).label("sessions"),
        func.sum(StrengthWorkout.total_volume_kg).label("volume"),
    ).first()
    total_sessions = agg.sessions or 0
    total_volume = agg.volume or 0

    recent_row = (
        db.query(StrengthWorkout.workout_date)
        .order_by(StrengthWorkout.workout_date.desc())
        .first()
    )
    most_recent = recent_row[0] if recent_row else None
    days_since_last = (date.today() - most_recent).days if most_recent else None

    top_group_row = (
        db.query(StrengthExercise.muscle_group)
        .group_by(StrengthExercise.muscle_group)
        .order_by(func.sum(StrengthExercise.total_volume_kg).desc())
        .first()
    )
    top_group = top_group_row[0] if top_group_row else "—"

    chart_data = _build_chart_data(db)
    period = chart_data["period_comparison"]

    return templates.TemplateResponse("strength_summary.html", {
        "request": request,
        "total_sessions": total_sessions,
        "total_volume_kg": int(total_volume),
        "most_recent": most_recent.strftime("%d %b %Y") if most_recent else "—",
        "days_since_last": days_since_last,
        "top_muscle_group": top_group or "—",
        "period": period,
        "chart_data_json": json.dumps(chart_data),
    })


@router.get("/log", response_class=HTMLResponse)
def strength_log(request: Request, db: Session = Depends(get_db)):
    workouts = (
        db.query(StrengthWorkout)
        .options(joinedload(StrengthWorkout.exercises).joinedload(StrengthExercise.sets))
        .order_by(StrengthWorkout.workout_date.desc())
        .all()
    )
    return templates.TemplateResponse("strength_log.html", {
        "request": request,
        "workouts": workouts,
        "fmt_duration": _fmt_duration,
    })


@router.get("/api/summary-data")
def strength_summary_data(db: Session = Depends(get_db)):
    return JSONResponse(_build_chart_data(db))


@router.get("/api/exercises")
def exercise_list(db: Session = Depends(get_db)):
    rows = (
        db.query(
            StrengthExercise.exercise_name,
            func.max(StrengthExercise.muscle_group).label("muscle_group"),
        )
        .group_by(StrengthExercise.exercise_name)
        .order_by(StrengthExercise.exercise_name)
        .all()
    )
    return [{"name": r.exercise_name, "muscle_group": r.muscle_group or "Other"} for r in rows]


def _build_chart_data(db: Session) -> dict:
    today = date.today()
    cutoff_28 = today - timedelta(days=28)
    cutoff_56 = today - timedelta(days=56)

    # --- Muscle group totals (all-time) ---
    group_rows = (
        db.query(
            StrengthExercise.muscle_group,
            func.sum(StrengthExercise.total_volume_kg).label("volume"),
            func.count(func.distinct(StrengthExercise.workout_id)).label("sessions"),
        )
        .group_by(StrengthExercise.muscle_group)
        .order_by(func.sum(StrengthExercise.total_volume_kg).desc())
        .all()
    )
    by_muscle_group = [
        {
            "group": r.muscle_group or "Other",
            "sessions": r.sessions,
            "total_volume_kg": round(r.volume or 0),
        }
        for r in group_rows
    ]

    # --- Weekly volume + sessions per week ---
    workouts = (
        db.query(StrengthWorkout.workout_date, StrengthWorkout.total_volume_kg)
        .order_by(StrengthWorkout.workout_date)
        .all()
    )
    weekly_vol: dict[str, float] = defaultdict(float)
    weekly_sessions: dict[str, int] = defaultdict(int)
    for w_date, vol in workouts:
        if w_date:
            iso = w_date.isocalendar()
            week_key = f"{iso.year}-W{iso.week:02d}"
            weekly_vol[week_key] += vol or 0
            weekly_sessions[week_key] += 1
    volume_over_time = [
        {"week": k, "volume_kg": round(v)} for k, v in sorted(weekly_vol.items())
    ]
    sessions_over_time = [
        {"week": k, "sessions": v} for k, v in sorted(weekly_sessions.items())
    ]

    # --- Exercise progress: estimated 1RM per session (Epley formula) ---
    set_rows = (
        db.query(
            StrengthExercise.exercise_name,
            StrengthExercise.muscle_group,
            StrengthWorkout.workout_date,
            StrengthSet.weight_kg,
            StrengthSet.reps,
        )
        .join(StrengthSet, StrengthSet.exercise_id == StrengthExercise.id)
        .join(StrengthWorkout, StrengthExercise.workout_id == StrengthWorkout.id)
        .filter(StrengthSet.weight_kg > 0, StrengthSet.reps > 0)
        .order_by(StrengthExercise.exercise_name, StrengthWorkout.workout_date)
        .all()
    )

    ex_session_best: dict[str, dict[str, dict]] = defaultdict(dict)
    for row in set_rows:
        name = row.exercise_name
        date_str = row.workout_date.strftime("%Y-%m-%d") if row.workout_date else None
        if not date_str:
            continue
        weight = row.weight_kg or 0
        reps = row.reps or 1
        est_1rm = round(weight * (1 + reps / 30.0), 1)
        if date_str not in ex_session_best[name]:
            ex_session_best[name][date_str] = {
                "est_1rm": est_1rm,
                "max_weight": weight,
                "muscle_group": row.muscle_group or "Other",
            }
        else:
            entry = ex_session_best[name][date_str]
            if est_1rm > entry["est_1rm"]:
                entry["est_1rm"] = est_1rm
            if weight > entry["max_weight"]:
                entry["max_weight"] = weight

    exercise_progress: dict[str, dict] = {}
    for ex_name, date_dict in ex_session_best.items():
        sorted_dates = sorted(date_dict.items())
        if len(sorted_dates) < 2:
            continue
        exercise_progress[ex_name] = {
            "muscle_group": sorted_dates[0][1]["muscle_group"],
            "sessions": len(sorted_dates),
            "data": [
                {"date": d, "est_1rm": v["est_1rm"], "max_weight": v["max_weight"]}
                for d, v in sorted_dates
            ],
        }

    # --- Period comparison (last 28 days vs previous 28 days) ---
    period_workouts = (
        db.query(StrengthWorkout.workout_date, StrengthWorkout.total_volume_kg)
        .filter(StrengthWorkout.workout_date >= cutoff_56)
        .all()
    )
    current_vol = sum(w.total_volume_kg or 0 for w in period_workouts if w.workout_date >= cutoff_28)
    prev_vol = sum(w.total_volume_kg or 0 for w in period_workouts if cutoff_56 <= w.workout_date < cutoff_28)
    current_sessions_n = sum(1 for w in period_workouts if w.workout_date >= cutoff_28)
    prev_sessions_n = sum(1 for w in period_workouts if cutoff_56 <= w.workout_date < cutoff_28)

    vol_change_pct = round(((current_vol - prev_vol) / prev_vol * 100) if prev_vol > 0 else 0)
    session_change_pct = round(((current_sessions_n - prev_sessions_n) / prev_sessions_n * 100) if prev_sessions_n > 0 else 0)

    period_comparison = {
        "current_volume": round(current_vol),
        "prev_volume": round(prev_vol),
        "current_sessions": current_sessions_n,
        "prev_sessions": prev_sessions_n,
        "volume_change_pct": vol_change_pct,
        "session_change_pct": session_change_pct,
    }

    # --- Exercise trends: compare first half vs second half avg 1RM ---
    exercise_trends = []
    for ex_name, ex_data in exercise_progress.items():
        pts = ex_data["data"]
        if len(pts) < 3:
            continue
        mid = len(pts) // 2
        first_avg = sum(p["est_1rm"] for p in pts[:mid]) / mid
        second_avg = sum(p["est_1rm"] for p in pts[mid:]) / (len(pts) - mid)
        change_pct = round((second_avg - first_avg) / first_avg * 100, 1) if first_avg > 0 else 0
        trend = "improving" if change_pct > 5 else "declining" if change_pct < -5 else "plateau"
        exercise_trends.append({
            "name": ex_name,
            "muscle_group": ex_data["muscle_group"],
            "trend": trend,
            "change_pct": change_pct,
            "current_1rm": pts[-1]["est_1rm"],
            "first_1rm": pts[0]["est_1rm"],
            "sessions": len(pts),
        })
    exercise_trends.sort(key=lambda x: x["change_pct"], reverse=True)

    # --- Muscle group recent vs previous 28-day sessions ---
    def _mg_period_query(date_from, date_to=None):
        q = (
            db.query(
                StrengthExercise.muscle_group,
                func.count(func.distinct(StrengthExercise.workout_id)).label("sessions"),
                func.sum(StrengthExercise.total_volume_kg).label("vol"),
            )
            .join(StrengthWorkout, StrengthExercise.workout_id == StrengthWorkout.id)
            .filter(StrengthWorkout.workout_date >= date_from)
        )
        if date_to:
            q = q.filter(StrengthWorkout.workout_date < date_to)
        return {r.muscle_group or "Other": {"sessions": r.sessions, "vol": round(r.vol or 0)}
                for r in q.group_by(StrengthExercise.muscle_group).all()}

    recent_mg = _mg_period_query(cutoff_28)
    prev_mg = _mg_period_query(cutoff_56, cutoff_28)

    vol_order = {mg["group"]: mg["total_volume_kg"] for mg in by_muscle_group}
    all_groups = sorted({mg["group"] for mg in by_muscle_group}, key=lambda g: vol_order.get(g, 0), reverse=True)

    muscle_analysis = []
    for group in all_groups:
        rec = recent_mg.get(group, {"sessions": 0, "vol": 0})
        prev = prev_mg.get(group, {"sessions": 0, "vol": 0})
        rec_s, prev_s = rec["sessions"], prev["sessions"]
        freq_change = round((rec_s - prev_s) / prev_s * 100) if prev_s > 0 else (100 if rec_s > 0 else 0)
        status = "neglected" if rec_s == 0 else ("on_track" if prev_s == 0 or rec_s >= prev_s * 0.7 else "needs_attention")
        muscle_analysis.append({
            "group": group,
            "recent_sessions": rec_s,
            "prev_sessions": prev_s,
            "recent_volume": rec["vol"],
            "prev_volume": prev["vol"],
            "freq_change_pct": freq_change,
            "status": status,
        })

    # --- Volume per muscle group per session (for multi-line progress chart) ---
    session_mg_rows = (
        db.query(
            StrengthWorkout.workout_date,
            StrengthExercise.muscle_group,
            func.sum(StrengthExercise.total_volume_kg).label("vol"),
        )
        .join(StrengthExercise, StrengthExercise.workout_id == StrengthWorkout.id)
        .filter(StrengthExercise.total_volume_kg > 0)
        .group_by(StrengthWorkout.workout_date, StrengthExercise.muscle_group)
        .order_by(StrengthWorkout.workout_date)
        .all()
    )
    mg_vol_by_group: dict[str, list] = defaultdict(list)
    for row in session_mg_rows:
        date_str = row.workout_date.strftime("%Y-%m-%d") if row.workout_date else None
        if not date_str:
            continue
        mg_vol_by_group[row.muscle_group or "Other"].append(
            {"date": date_str, "vol": round(row.vol or 0)}
        )
    muscle_group_volume = {
        group: sorted(sessions, key=lambda x: x["date"])
        for group, sessions in mg_vol_by_group.items()
    }

    return {
        "by_muscle_group": by_muscle_group,
        "volume_over_time": volume_over_time,
        "sessions_over_time": sessions_over_time,
        "muscle_group_volume": muscle_group_volume,
        "period_comparison": period_comparison,
        "exercise_trends": exercise_trends,
        "muscle_analysis": muscle_analysis,
    }
