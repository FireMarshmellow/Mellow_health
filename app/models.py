from sqlalchemy import Column, BigInteger, String, Float, Integer, Boolean, DateTime, JSON
from sqlalchemy.sql import func
from database import Base


class Activity(Base):
    __tablename__ = "activities"

    id = Column(Integer, primary_key=True, autoincrement=True)
    strava_id = Column(BigInteger, unique=True, nullable=False, index=True)
    source = Column(String(50), default="strava")
    name = Column(String(255))
    sport_type = Column(String(50))
    start_date = Column(DateTime(timezone=False))
    distance_meters = Column(Float)
    duration_seconds = Column(Integer)
    moving_time_seconds = Column(Integer)
    elevation_gain = Column(Float)
    avg_pace_sec_per_km = Column(Float)
    avg_speed_kmh = Column(Float)
    avg_heart_rate = Column(Float)
    max_heart_rate = Column(Integer)
    calories = Column(Integer)
    has_gps = Column(Boolean, default=False)
    gps_track = Column(JSON)
    source_file = Column(String(255))
    created_at = Column(DateTime, server_default=func.now())
