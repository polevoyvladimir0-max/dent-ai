import os
from pathlib import Path
from datetime import datetime
from typing import Optional

from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine, JSON, Float, Boolean, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

echo = False

BASE_DIR = Path(os.getenv("DENT_AI_BASE", Path(__file__).resolve().parents[1]))


def _make_engine():
    url = os.getenv("DATABASE_URL")
    if url:
        if url.startswith("sqlite:///"):
            sqlite_path = Path(url.split("sqlite:///", 1)[1])
            if not sqlite_path.is_absolute():
                sqlite_path = BASE_DIR / sqlite_path
            sqlite_path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{sqlite_path}"
        return create_engine(url, echo=echo, future=True)

    sqlite_location = Path(os.getenv("SQLITE_PATH", BASE_DIR / "storage" / "state.db"))
    if not sqlite_location.is_absolute():
        sqlite_location = BASE_DIR / sqlite_location
    sqlite_location.parent.mkdir(parents=True, exist_ok=True)
    return create_engine(f"sqlite:///{sqlite_location}", echo=echo, future=True)


engine = _make_engine()
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


class PlanTemplate(Base):
    """Шаблон плана лечения, обученный на предпочтениях врача.
    
    Связывает идеологический план (диктовка врача) с последовательностью кодов.
    Используется для обучения модели на предпочтениях врача.
    """
    __tablename__ = "plan_templates"

    id = Column(Integer, primary_key=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    
    # Идеологический план (диктовка врача)
    ideological_plan = Column(Text, nullable=False)
    
    # Последовательность кодов (JSON массив строк)
    codes_sequence = Column(JSON, nullable=False)
    
    # Метаданные плана (бренды, локализация, особенности)
    # Пример: {"brands": ["Straumann"], "location": "зуб 2.5", "features": ["одномоментная"]}
    plan_metadata = Column("metadata", JSON, default=dict)
    
    # ID точки в Qdrant для векторного поиска
    qdrant_point_id = Column(Integer, unique=True, nullable=True)
    
    # Счётчик использования (для приоритизации в поиске)
    usage_count = Column(Integer, default=1)
    
    # Связанный plan_id (если шаблон создан из конкретного плана)
    source_plan_id = Column(Integer, ForeignKey("treatment_plans.id"), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    doctor = relationship("Doctor")
    source_plan = relationship("TreatmentPlan")

def init_db():
    Base.metadata.create_all(bind=engine)
    if engine.dialect.name == "sqlite":
        _apply_sqlite_migrations()


def _apply_sqlite_migrations():
    def ensure_column(table: str, column: str, ddl: str):
        inspector = inspect(engine)
        if column not in {col["name"] for col in inspector.get_columns(table)}:
            with engine.begin() as conn:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))

    ensure_column("doctors", "specialization", "specialization VARCHAR(128)")
    ensure_column("doctors", "experience_years", "experience_years FLOAT")
    ensure_column("doctors", "preferences", "preferences JSON")
    ensure_column("treatment_plans", "agent_plan", "agent_plan TEXT")
    ensure_column("treatment_plans", "agent_validation", "agent_validation JSON")
    
    # Создаём таблицу plan_templates, если её нет
    inspector = inspect(engine)
    if "plan_templates" not in inspector.get_table_names():
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS plan_templates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doctor_id INTEGER NOT NULL REFERENCES doctors(id),
                    ideological_plan TEXT NOT NULL,
                    codes_sequence TEXT NOT NULL,
                    metadata TEXT DEFAULT '{}',
                    qdrant_point_id INTEGER UNIQUE,
                    usage_count INTEGER DEFAULT 1,
                    source_plan_id INTEGER REFERENCES treatment_plans(id),
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """))
            # Создаём индексы для быстрого поиска
            try:
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_plan_templates_doctor ON plan_templates(doctor_id)"))
                conn.execute(text("CREATE INDEX IF NOT EXISTS idx_plan_templates_qdrant ON plan_templates(qdrant_point_id)"))
            except Exception:
                pass  # Индексы могут уже существовать
