from sqlalchemy import Column, BigInteger, String, Float, Integer, Boolean, DateTime, JSON, Date, Time, ForeignKey, UniqueConstraint
from sqlalchemy.orm import relationship
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


class StrengthWorkout(Base):
    __tablename__ = "strength_workouts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workout_name = Column(String(255), nullable=False)
    workout_date = Column(Date, nullable=False)
    workout_time = Column(Time, nullable=True)
    duration_seconds = Column(Integer, nullable=True)
    total_volume_kg = Column(Float, nullable=True)
    muscle_group = Column(String(100), nullable=True)
    source_file     = Column(String(255))
    plan_workout_id = Column(Integer, ForeignKey("workout_plan_days.id"), nullable=True, index=True)
    created_at      = Column(DateTime, server_default=func.now())

    exercises = relationship("StrengthExercise", back_populates="workout", cascade="all, delete-orphan", order_by="StrengthExercise.exercise_order")

    __table_args__ = (UniqueConstraint("workout_date", "workout_name", name="uq_strength_workout"),)


class StrengthExercise(Base):
    __tablename__ = "strength_exercises"

    id = Column(Integer, primary_key=True, autoincrement=True)
    workout_id = Column(Integer, ForeignKey("strength_workouts.id"), nullable=False)
    exercise_name = Column(String(255), nullable=False)
    exercise_order = Column(Integer, nullable=False, default=0)
    total_volume_kg = Column(Float, nullable=True)

    muscle_group = Column(String(100), nullable=True)

    workout = relationship("StrengthWorkout", back_populates="exercises")
    sets = relationship("StrengthSet", back_populates="exercise", cascade="all, delete-orphan", order_by="StrengthSet.set_number")


class StrengthSet(Base):
    __tablename__ = "strength_sets"

    id = Column(Integer, primary_key=True, autoincrement=True)
    exercise_id = Column(Integer, ForeignKey("strength_exercises.id"), nullable=False)
    set_number = Column(Integer, nullable=False)
    weight_kg = Column(Float, nullable=True)
    reps = Column(Integer, nullable=False)

    exercise = relationship("StrengthExercise", back_populates="sets")


class WorkoutPlan(Base):
    __tablename__ = "workout_plans"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    plan_name   = Column(String(255), nullable=False)
    raw_json    = Column(JSON, nullable=False)
    uploaded_at = Column(DateTime, server_default=func.now())
    is_active   = Column(Boolean, default=True)

    plan_workouts = relationship(
        "WorkoutPlanDay",
        back_populates="plan",
        cascade="all, delete-orphan",
        order_by="WorkoutPlanDay.day_index",
    )


class WorkoutPlanDay(Base):
    __tablename__ = "workout_plan_days"

    id           = Column(Integer, primary_key=True, autoincrement=True)
    plan_id      = Column(Integer, ForeignKey("workout_plans.id"), nullable=False)
    day_index    = Column(Integer, nullable=False)
    day_label    = Column(String(50))
    workout_name = Column(String(255), nullable=False)
    muscle_group = Column(String(100), nullable=True)
    planned_json = Column(JSON, nullable=False)

    plan = relationship("WorkoutPlan", back_populates="plan_workouts")
