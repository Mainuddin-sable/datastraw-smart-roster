from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, JSON, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class AuditLog(Base):
    """Append-oriented event record; entity_id is polymorphic and not an FK."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_entity", "entity_type", "entity_id", "created_at"),
        CheckConstraint(
            "(actor_employee_id IS NULL AND actor_organization_id IS NULL) "
            "OR (actor_employee_id IS NOT NULL AND actor_organization_id = organization_id)",
            name="ck_audit_log_actor_scope",
        ),
        ForeignKeyConstraint(
            ["actor_employee_id", "actor_organization_id"],
            ["employees.id", "employees.organization_id"],
            ondelete="SET NULL", name="fk_audit_log_actor_tenant",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False, index=True)
    actor_employee_id: Mapped[int | None] = mapped_column(nullable=True, index=True)
    actor_organization_id: Mapped[int | None] = mapped_column(nullable=True)
    entity_type: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_id: Mapped[int] = mapped_column(nullable=False)
    action: Mapped[str] = mapped_column(String(60), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    organization: Mapped["Organization"] = relationship(back_populates="audit_logs")
