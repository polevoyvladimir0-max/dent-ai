from pathlib import Path
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine, JSON, Float, Boolean, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

DB_PATH = Path(r"C:\dent_ai\storage\state.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

echo = False
engine = create_engine(f"sqlite:///{DB_PATH}", echo=echo, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()

class Doctor(Base):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    telegram_id = Column(String(64), unique=True)
    specialization = Column(String(128))
    experience_years = Column(Float)
    preferences = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("Session", back_populates="doctor")
    profiles = relationship("DoctorProfile", back_populates="doctor", cascade="all, delete-orphan")
    feedback = relationship("PlanFeedback", back_populates="doctor", cascade="all, delete-orphan")

class Patient(Base):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True)
    name = Column(String(128), nullable=False)
    card_number = Column(String(64))
    created_at = Column(DateTime, default=datetime.utcnow)

    sessions = relationship("Session", back_populates="patient")

class Session(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    status = Column(String(32), default="draft")
    transcript = Column(Text)
    codes = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    doctor = relationship("Doctor", back_populates="sessions")
    patient = relationship("Patient", back_populates="sessions")
    plans = relationship("TreatmentPlan", back_populates="session")

class TreatmentPlan(Base):
    __tablename__ = "treatment_plans"

    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("sessions.id"), nullable=False)
    plan_json = Column(JSON, nullable=False)
    pdf_path = Column(String(256))
    status = Column(String(32), default="draft")
    agent_plan = Column(Text)
    agent_validation = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    session = relationship("Session", back_populates="plans")
    feedback = relationship("PlanFeedback", back_populates="plan", cascade="all, delete-orphan")


class DoctorProfile(Base):
    __tablename__ = "doctor_profiles"

    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    profile_name = Column(String(64), nullable=False)
    llm_prompt = Column(Text)
    pricing_bias = Column(JSON, default=dict)
    protocol_overrides = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    doctor = relationship("Doctor", back_populates="profiles")


class PlanFeedback(Base):
    __tablename__ = "plan_feedback"

    id = Column(Integer, primary_key=True)
    plan_id = Column(Integer, ForeignKey("treatment_plans.id"), nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    rating = Column(Integer)
    accepted = Column(Boolean, default=False)
    comments = Column(Text)
    diff_json = Column(JSON)
    created_at = Column(DateTime, default=datetime.utcnow)

    plan = relationship("TreatmentPlan", back_populates="feedback")
    doctor = relationship("Doctor", back_populates="feedback")

def init_db():
    Base.metadata.create_all(bind=engine)
    _apply_sqlite_migrations()


def _apply_sqlite_migrations():
    inspector = inspect(engine)

    def ensure_column(table: str, column: str, ddl: str):
        if column not in {col["name"] for col in inspector.get_columns(table)}:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))

    ensure_column("doctors", "specialization", "specialization VARCHAR(128)")
    ensure_column("doctors", "experience_years", "experience_years FLOAT")
    ensure_column("doctors", "preferences", "preferences JSON")
    ensure_column("treatment_plans", "agent_plan", "agent_plan TEXT")
    ensure_column("treatment_plans", "agent_validation", "agent_validation JSON")
