"""All SQLAlchemy ORM models. FSE-A owns this."""
from sqlalchemy import Column, Integer, String, Float, Text, Boolean, LargeBinary, DateTime, ForeignKey
from sqlalchemy.sql import func
from db.engine import Base


class User(Base):
    __tablename__ = "users"
    id             = Column(Integer, primary_key=True)
    name           = Column(String, nullable=False)
    email          = Column(String, unique=True, index=True, nullable=False)
    hashed_pw      = Column(String, nullable=True)   # nullable for SSO-only accounts
    role           = Column(String, default="developer")
    is_active      = Column(Boolean, default=False)
    otp            = Column(String, nullable=True)
    otp_expiry     = Column(DateTime, nullable=True)
    oauth_provider = Column(String, nullable=True)   # "google" | "github" | "microsoft"
    oauth_id       = Column(String, nullable=True)   # provider user ID
    created_at     = Column(DateTime, server_default=func.now())


class VoiceEnrollment(Base):
    __tablename__ = "voice_enrollments"
    id           = Column(Integer, primary_key=True)
    user_id      = Column(Integer, ForeignKey("users.id"), index=True)
    speaker_name = Column(String, nullable=False)
    role         = Column(String, nullable=False)
    embedding    = Column(LargeBinary, nullable=True)
    npy_path     = Column(String, nullable=True)
    status       = Column(String, default="pending")  # pending|ready|failed
    created_at   = Column(DateTime, server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"
    id         = Column(Integer, primary_key=True)
    session_id = Column(String, unique=True, index=True, nullable=False)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=True)
    usecase    = Column(String, nullable=False)    # ppt | customercare
    state      = Column(String, default="IDLE")
    snapshot   = Column(Text, nullable=True)       # JSON ring buffer snapshot
    created_at = Column(DateTime, server_default=func.now())
    ended_at   = Column(DateTime, nullable=True)


class TranscriptLog(Base):
    __tablename__ = "transcript_log"
    id         = Column(Integer, primary_key=True)
    session_id = Column(String, index=True)
    speaker_id = Column(String, nullable=True)
    role       = Column(String, nullable=True)
    text       = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    timestamp  = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class Ticket(Base):
    __tablename__ = "tickets"
    id         = Column(Integer, primary_key=True)
    ticket_ref = Column(String, unique=True)
    session_id = Column(String, nullable=True)
    category   = Column(String, nullable=True)
    synopsis   = Column(Text, nullable=True)
    symptoms   = Column(Text, nullable=True)
    status     = Column(String, default="open")
    resolution = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())


class KBDocument(Base):
    __tablename__ = "kb_documents"
    id      = Column(Integer, primary_key=True)
    title   = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags    = Column(String, nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id          = Column(Integer, primary_key=True)
    session_id  = Column(String, index=True)
    speaker_id  = Column(String, nullable=True)
    role        = Column(String, nullable=True)
    confidence  = Column(Float, nullable=True)
    action      = Column(String, nullable=False)
    tool        = Column(String, nullable=True)
    decision    = Column(String, nullable=False)
    detail      = Column(Text, nullable=True)
    latency_ms  = Column(Float, nullable=True)
    timestamp   = Column(DateTime, server_default=func.now())


class PPTSlideVersion(Base):
    __tablename__ = "ppt_slide_versions"
    id          = Column(Integer, primary_key=True)
    session_id  = Column(String, index=True, nullable=False)
    slide_index = Column(Integer, nullable=False)
    title       = Column(Text, nullable=True)
    bullets     = Column(Text, nullable=True)  # JSON-encoded list of strings
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())
