from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import EmployeeLeaveCreate, EmployeeLeaveRead, EmployeeLeaveStatusUpdate
from app.services import leave_management


router = APIRouter(prefix="/organizations/{organization_id}/leave-requests", tags=["leave requests"])
DbSession = Annotated[Session, Depends(get_db)]
OrgId = Annotated[int, Path(gt=0)]
LeaveId = Annotated[int, Path(gt=0)]
LeaveStatus = Literal["requested", "approved", "rejected", "cancelled"]


@router.get("", response_model=list[EmployeeLeaveRead])
def list_requests(
    organization_id: OrgId,
    db: DbSession,
    leave_status: Annotated[LeaveStatus | None, Query(alias="status")] = None,
):
    """List an organization's leave requests, optionally by workflow status."""
    return leave_management.list_leave_requests(db, organization_id, leave_status)


@router.post("", response_model=EmployeeLeaveRead, status_code=status.HTTP_201_CREATED)
def create_request(organization_id: OrgId, payload: EmployeeLeaveCreate, db: DbSession):
    """Create a pending leave request for an active employee in this organization."""
    return leave_management.create_leave_request(db, organization_id, payload.model_dump())


@router.patch("/{leave_id}", response_model=EmployeeLeaveRead)
def update_request_status(
    organization_id: OrgId,
    leave_id: LeaveId,
    payload: EmployeeLeaveStatusUpdate,
    db: DbSession,
):
    """Approve, reject, or cancel a leave request under the allowed state transitions."""
    return leave_management.update_leave_status(
        db, organization_id, leave_id, payload.status,
    )
