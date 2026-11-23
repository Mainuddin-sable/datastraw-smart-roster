from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import (
    GenerateMonthlyRosterRequest, MonthlyRosterGenerationRead, RosterAssignmentEdit,
    RosterAssignmentLock, RosterValidationRead,
)
from app.services import roster_generation as service
from app.services import roster_validation
from app.services.organization_structure import ResourceNotFound
from app.services.roster_generation import LockedAssignmentConflict

router = APIRouter(prefix="/organizations/{organization_id}/rosters", tags=["roster generation"])
DbSession = Annotated[Session, Depends(get_db)]
OrgId = Annotated[int, Path(gt=0)]


@router.post(
    "/generate",
    response_model=MonthlyRosterGenerationRead,
    status_code=status.HTTP_201_CREATED,
    responses={
        404: {"description": "Pod or Process not found in the organization and scope"},
        409: {"model": MonthlyRosterGenerationRead, "description": "Hard staffing constraints are infeasible"},
        422: {"model": MonthlyRosterGenerationRead, "description": "Scheduling configuration is incomplete or invalid"},
        503: {"model": MonthlyRosterGenerationRead, "description": "Solver time limit expired without a feasible solution"},
    },
)
def generate_monthly_roster(
    organization_id: OrgId,
    payload: GenerateMonthlyRosterRequest,
    db: DbSession,
):
    """Generate and persist a monthly Pod roster using a bounded CP-SAT solve."""
    try:
        result = service.generate_monthly_roster(
            db,
            organization_id=organization_id,
            pod_id=payload.pod_id,
            year=payload.year,
            month=payload.month,
            process_id=payload.process_id,
            time_limit_seconds=payload.solver_time_limit_seconds,
            preserve_locks_from_roster_id=payload.preserve_locks_from_roster_id,
        )
    except ResourceNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except LockedAssignmentConflict as exc:
        return JSONResponse(status_code=409, content=jsonable_encoder({
            "detail": str(exc), "unresolved_conflicts": exc.report["conflicts"],
        }))
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result["success"]:
        return result
    status_code = {
        "infeasible": status.HTTP_409_CONFLICT,
        "unknown": status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(result["status"], 422)
    return JSONResponse(status_code=status_code, content=jsonable_encoder(result))


@router.post("/{roster_id}/validate", response_model=RosterValidationRead)
def validate_saved_roster(organization_id: OrgId, roster_id: Annotated[int, Path(gt=0)], db: DbSession):
    try:
        return roster_validation.validate_roster(db, organization_id, roster_id)
    except ResourceNotFound as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{roster_id}/revalidate", response_model=RosterValidationRead)
def revalidate_roster(organization_id: OrgId, roster_id: Annotated[int, Path(gt=0)], db: DbSession):
    """Recheck a saved roster after availability, leave or other source data changed."""
    return validate_saved_roster(organization_id, roster_id, db)


@router.put("/{roster_id}/assignments/{assignment_id}", status_code=200)
def edit_roster_assignment(
    organization_id: OrgId,
    roster_id: Annotated[int, Path(gt=0)],
    assignment_id: Annotated[int, Path(gt=0)],
    payload: RosterAssignmentEdit,
    db: DbSession,
):
    try:
        return roster_validation.edit_assignment(
            db, organization_id, roster_id, assignment_id,
            payload.employee_id, payload.process_assignment_id,
        )
    except ResourceNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.patch("/{roster_id}/assignments/{assignment_id}/lock", status_code=200)
def lock_roster_assignment(
    organization_id: OrgId,
    roster_id: Annotated[int, Path(gt=0)],
    assignment_id: Annotated[int, Path(gt=0)],
    payload: RosterAssignmentLock,
    db: DbSession,
):
    try:
        return roster_validation.set_assignment_lock(
            db, organization_id, roster_id, assignment_id, payload.is_locked
        )
    except ResourceNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
