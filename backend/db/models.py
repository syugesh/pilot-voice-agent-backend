"""All SQLAlchemy ORM models. FSE-A owns this."""

from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.sql import func

from backend.db.engine import Base


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_pw = Column(String, nullable=False)
    role = Column(String, default="developer")
    is_active = Column(Boolean, default=False)
    status = Column(String, default="offline")
    otp = Column(String, nullable=True)
    otp_expiry = Column(DateTime, nullable=True)
    embedding = Column(LargeBinary, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class VoiceEnrollment(Base):
    __tablename__ = "voice_enrollments"
    id = Column(Integer, primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), index=True)
    speaker_name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    embedding = Column(LargeBinary, nullable=True)
    npy_path = Column(String, nullable=True)
    status = Column(String, default="pending")  # pending|ready|failed
    created_at = Column(DateTime, server_default=func.now())


class Session(Base):
    __tablename__ = "sessions"
    id = Column(Integer, primary_key=True)
    session_id = Column(String, unique=True, index=True, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)
    usecase = Column(String, nullable=False)  # ppt | customercare
    state = Column(String, default="IDLE")
    snapshot = Column(Text, nullable=True)  # JSON ring buffer snapshot
    summary = Column(Text, nullable=True)  # AI-generated short session summary
    bullets = Column(Text, nullable=True)  # JSON list of action bullet points
    created_at = Column(DateTime, server_default=func.now())
    ended_at = Column(DateTime, nullable=True)


class TranscriptLog(Base):
    __tablename__ = "transcript_log"
    id = Column(Integer, primary_key=True)
    session_id = Column(String, index=True)
    speaker_id = Column(String, nullable=True)
    role = Column(String, nullable=True)
    text = Column(Text, nullable=False)
    confidence = Column(Float, default=0.0)
    timestamp = Column(Float, nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class KBDocument(Base):
    __tablename__ = "kb_documents"
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    tags = Column(String, nullable=True)


class Ticket(Base):
    __tablename__ = "tickets"
    id = Column(Integer, primary_key=True)
    ticket_ref = Column(String, unique=True)
    session_id = Column(String, nullable=True)
    category = Column(String, nullable=True)
    synopsis = Column(Text, nullable=True)
    symptoms = Column(Text, nullable=True)
    status = Column(String, default="open")
    resolution = Column(Text, nullable=True)
    priority = Column(String, default="normal")  # normal | high | urgent
    escalated = Column(Boolean, default=False)
    escalation_target = Column(String, nullable=True)  # e.g. "L2 Technician"
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, onupdate=func.now())


class AuditLog(Base):
    __tablename__ = "audit_log"
    id = Column(Integer, primary_key=True)
    session_id = Column(String, index=True)
    speaker_id = Column(String, nullable=True)
    role = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    action = Column(String, nullable=False)
    tool = Column(String, nullable=True)
    decision = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    latency_ms = Column(Float, nullable=True)
    timestamp = Column(DateTime, server_default=func.now())


class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(String, nullable=True)
    created_by = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, server_default=func.now())


class GroupMember(Base):
    __tablename__ = "group_members"
    id = Column(Integer, primary_key=True)
    group_id = Column(Integer, ForeignKey("groups.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(String, default="member")  # "admin" | "member"


class PPTSlideVersion(Base):
    __tablename__ = "ppt_slide_versions"
    id          = Column(Integer, primary_key=True)
    session_id  = Column(String, index=True, nullable=False)
    slide_index = Column(Integer, nullable=False)
    title       = Column(Text, nullable=True)
    bullets     = Column(Text, nullable=True)  # JSON-encoded list of strings
    notes       = Column(Text, nullable=True)
    created_at  = Column(DateTime, server_default=func.now())
    joined_at = Column(DateTime, server_default=func.now())
