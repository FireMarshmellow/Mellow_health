import json

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func
from sqlalchemy.orm import Session

from database import get_db
from models import Activity

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/summary", response_class=HTMLResponse)
def summary_page(request: Request, db: Session = Depends(get_db)):
    stats = db.query(
        func.count(Activity.id).label("total"),
        func.sum(Activity.distance_meters).label("total_dist"),
        func.sum(Activity.moving_time_seconds).label("total_time"),
        func.sum(Activity.elevation_gain).label("total_ele"),
    ).first()

    gps_activities = (
        db.query(Activity.name, Activity.sport_type, Activity.gps_track)
        .filter(Activity.has_gps.is_(True))
        .all()
    )

    tracks = []
    for act in gps_activities:
        if act.gps_track:
            tracks.append({
                "name": act.name,
                "sport_type": act.sport_type,
                "points": [[p["lat"], p["lon"]] for p in act.gps_track],
            })

    total_dist_km = round((stats.total_dist or 0) / 1000, 1)
    total_hours = round((stats.total_time or 0) / 3600, 1)
    total_ele = int(stats.total_ele or 0)

    return templates.TemplateResponse("summary.html", {
        "request": request,
        "total_activities": stats.total or 0,
        "total_distance_km": total_dist_km,
        "total_time_hours": total_hours,
        "total_elevation": total_ele,
        "tracks_json": json.dumps(tracks),
    })


@router.get("/log", response_class=HTMLResponse)
def log_page(
    request: Request,
    db: Session = Depends(get_db),
    sort: str = "date",
    order: str = "desc",
    sport: str = "",
):
    query = db.query(Activity)

    if sport:
        query = query.filter(Activity.sport_type == sport)

    sort_map = {
        "date":      Activity.start_date,
        "distance":  Activity.distance_meters,
        "duration":  Activity.moving_time_seconds,
        "pace":      Activity.avg_pace_sec_per_km,
        "elevation": Activity.elevation_gain,
        "hr":        Activity.avg_heart_rate,
    }
    sort_col = sort_map.get(sort, Activity.start_date)
    if order == "desc":
        query = query.order_by(sort_col.desc().nulls_last())
    else:
        query = query.order_by(sort_col.asc().nulls_first())

    activities = query.all()
    sport_types = sorted(
        r[0] for r in db.query(Activity.sport_type).distinct().all() if r[0]
    )

    return templates.TemplateResponse("log.html", {
        "request": request,
        "activities": activities,
        "sport_types": sport_types,
        "current_sport": sport,
        "current_sort": sort,
        "current_order": order,
    })


@router.get("/api/activity/{activity_id}/track")
def activity_track(activity_id: int, db: Session = Depends(get_db)):
    act = db.query(
        Activity.has_gps,
        Activity.gps_track,
        Activity.sport_type,
    ).filter(Activity.id == activity_id).first()

    if act is None:
        return JSONResponse({"has_gps": False, "points": [], "sport_type": ""}, status_code=404)

    if not act.has_gps or not act.gps_track:
        return JSONResponse({"has_gps": False, "points": [], "sport_type": act.sport_type or ""})

    points = [[p["lat"], p["lon"]] for p in act.gps_track]
    return JSONResponse({"has_gps": True, "points": points, "sport_type": act.sport_type or ""})
