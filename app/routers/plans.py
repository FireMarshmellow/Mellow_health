import json
from datetime import date, time
from typing import Optional

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.exceptions import HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import get_db
from models import StrengthExercise, StrengthSet, StrengthWorkout, WorkoutPlan, WorkoutPlanDay
from parsers.pdf_parser import infer_muscle_group, normalize_muscle_group

router = APIRouter(prefix="/strength/plans")
templates = Jinja2Templates(directory="templates")


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class SetIn(BaseModel):
    weight_kg: Optional[float] = None
    reps: int


class ExerciseIn(BaseModel):
    name: str
    sets: list[SetIn]


class WorkoutSubmission(BaseModel):
    plan_workout_id: int
    workout_date: date
    workout_time: Optional[str] = None
    duration_seconds: int
    exercises: list[ExerciseIn]


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
def plans_list(request: Request, db: Session = Depends(get_db)):
    plans = db.query(WorkoutPlan).order_by(WorkoutPlan.uploaded_at.desc()).all()
    completion_counts = {}
    for plan in plans:
        day_ids = [d.id for d in plan.plan_workouts]
        done = (
            db.query(StrengthWorkout)
            .filter(StrengthWorkout.plan_workout_id.in_(day_ids))
            .count()
        ) if day_ids else 0
        completion_counts[plan.id] = {"done": done, "total": len(day_ids)}
    return templates.TemplateResponse("plans_list.html", {
        "request": request,
        "plans": plans,
        "completion_counts": completion_counts,
    })


@router.get("/upload", response_class=HTMLResponse)
def plans_upload_page(request: Request, error: str = ""):
    return templates.TemplateResponse("plans_upload.html", {
        "request": request,
        "error": error,
    })


@router.post("/upload")
async def plans_upload(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    raw = await file.read()
    try:
        data = json.loads(raw)
    except Exception:
        return templates.TemplateResponse("plans_upload.html", {
            "request": request,
            "error": "Invalid JSON file — could not parse.",
        }, status_code=400)

    if not isinstance(data, dict) or "plan_name" not in data or "workouts" not in data:
        return templates.TemplateResponse("plans_upload.html", {
            "request": request,
            "error": 'JSON must have top-level keys "plan_name" and "workouts".',
        }, status_code=400)

    workouts = data.get("workouts", [])
    if not isinstance(workouts, list) or len(workouts) == 0:
        return templates.TemplateResponse("plans_upload.html", {
            "request": request,
            "error": '"workouts" must be a non-empty list.',
        }, status_code=400)

    for i, w in enumerate(workouts):
        if not isinstance(w, dict) or "workout_name" not in w or "exercises" not in w:
            return templates.TemplateResponse("plans_upload.html", {
                "request": request,
                "error": f'Workout #{i+1} must have "workout_name" and "exercises" keys.',
            }, status_code=400)

    plan = WorkoutPlan(plan_name=data["plan_name"], raw_json=data)
    db.add(plan)
    db.flush()

    for idx, w in enumerate(workouts):
        raw_muscle = w.get("muscle_group")
        muscle = (normalize_muscle_group(raw_muscle) if raw_muscle
                  else infer_muscle_group(w["workout_name"]) if w["workout_name"]
                  else None)
        # Normalize per-exercise muscle_group fields so tracker displays canonical labels
        raw_exercises = w.get("exercises", [])
        exercises = [
            {**ex, "muscle_group": normalize_muscle_group(ex["muscle_group"])}
            if ex.get("muscle_group") else ex
            for ex in raw_exercises
        ]
        day = WorkoutPlanDay(
            plan_id=plan.id,
            day_index=idx,
            day_label=w.get("day_label", f"Day {idx + 1}"),
            workout_name=w["workout_name"],
            muscle_group=muscle,
            planned_json={"exercises": exercises},
        )
        db.add(day)

    db.commit()
    return RedirectResponse(url=f"/strength/plans/{plan.id}", status_code=303)


@router.get("/{plan_id}", response_class=HTMLResponse)
def plan_overview(request: Request, plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(WorkoutPlan).filter(WorkoutPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    day_ids = [d.id for d in plan.plan_workouts]
    completed_rows = (
        db.query(StrengthWorkout.plan_workout_id)
        .filter(StrengthWorkout.plan_workout_id.in_(day_ids))
        .all()
    ) if day_ids else []
    completed_day_ids = {r[0] for r in completed_rows}

    exercises_by_day = {
        day.id: json.dumps(day.planned_json.get("exercises", []))
        for day in plan.plan_workouts
    }

    return templates.TemplateResponse("plan_overview.html", {
        "request": request,
        "plan": plan,
        "days": plan.plan_workouts,
        "completed_day_ids": completed_day_ids,
        "exercises_by_day": exercises_by_day,
    })


@router.get("/{plan_id}/day/{day_id}", response_class=HTMLResponse)
def tracker_page(
    request: Request,
    plan_id: int,
    day_id: int,
    db: Session = Depends(get_db),
):
    day = db.query(WorkoutPlanDay).filter(WorkoutPlanDay.id == day_id).first()
    if not day or day.plan_id != plan_id:
        raise HTTPException(status_code=404, detail="Workout day not found")

    return templates.TemplateResponse("tracker.html", {
        "request": request,
        "day": day,
        "plan_id": plan_id,
        "exercises": day.planned_json.get("exercises", []),
    })


@router.delete("/{plan_id}")
def delete_plan(plan_id: int, db: Session = Depends(get_db)):
    plan = db.query(WorkoutPlan).filter(WorkoutPlan.id == plan_id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")
    # Detach any completed workouts so they survive the plan deletion
    day_ids = [d.id for d in plan.plan_workouts]
    if day_ids:
        db.query(StrengthWorkout).filter(
            StrengthWorkout.plan_workout_id.in_(day_ids)
        ).update({"plan_workout_id": None}, synchronize_session=False)
    db.delete(plan)
    db.commit()
    return {"ok": True}


@router.delete("/{plan_id}/day/{day_id}")
def delete_plan_day(plan_id: int, day_id: int, db: Session = Depends(get_db)):
    day = db.query(WorkoutPlanDay).filter(
        WorkoutPlanDay.id == day_id,
        WorkoutPlanDay.plan_id == plan_id,
    ).first()
    if not day:
        raise HTTPException(status_code=404, detail="Day not found")
    completed = db.query(StrengthWorkout).filter(StrengthWorkout.plan_workout_id == day_id).first()
    if completed:
        raise HTTPException(status_code=409, detail="Cannot delete a completed workout day")
    db.delete(day)
    db.commit()
    return {"ok": True}


@router.post("/api/submit-workout")
async def submit_workout(payload: WorkoutSubmission, db: Session = Depends(get_db)):
    day = db.query(WorkoutPlanDay).filter(WorkoutPlanDay.id == payload.plan_workout_id).first()
    if not day:
        raise HTTPException(status_code=404, detail="Plan day not found")

    workout_time = None
    if payload.workout_time:
        try:
            h, m = payload.workout_time.split(":")
            workout_time = time(int(h), int(m))
        except Exception:
            pass

    workout = StrengthWorkout(
        workout_name=day.workout_name,
        workout_date=payload.workout_date,
        workout_time=workout_time,
        duration_seconds=payload.duration_seconds,
        muscle_group=day.muscle_group,
        source_file="live_tracker",
        plan_workout_id=payload.plan_workout_id,
    )
    db.add(workout)

    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f'A workout named "{day.workout_name}" already exists for {payload.workout_date}. '
                   "Change the date or delete the existing entry first.",
        )

    total_volume = 0.0
    for ex_idx, ex in enumerate(payload.exercises):
        ex_volume = sum(
            (s.weight_kg or 0) * s.reps
            for s in ex.sets
            if s.weight_kg is not None
        )
        total_volume += ex_volume
        exercise = StrengthExercise(
            workout_id=workout.id,
            exercise_name=ex.name,
            exercise_order=ex_idx,
            muscle_group=infer_muscle_group(ex.name),
            total_volume_kg=ex_volume if ex_volume > 0 else None,
        )
        db.add(exercise)
        db.flush()

        for set_idx, s in enumerate(ex.sets):
            db.add(StrengthSet(
                exercise_id=exercise.id,
                set_number=set_idx + 1,
                weight_kg=s.weight_kg,
                reps=s.reps,
            ))

    workout.total_volume_kg = total_volume if total_volume > 0 else None
    db.commit()

    return {"ok": True, "redirect": f"/strength/plans/{day.plan_id}"}
