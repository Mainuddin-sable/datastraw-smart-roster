from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import EffectiveStaffingRequirementRead, StaffingRequirementInput, StaffingRequirementRead
from app.services import staffing as service

router = APIRouter(prefix="/organizations/{organization_id}/staffing-requirements", tags=["staffing requirements"])
DbSession = Annotated[Session, Depends(get_db)]
OrgId = Annotated[int, Path(gt=0)]
RequirementId = Annotated[int, Path(gt=0)]


@router.post("", response_model=StaffingRequirementRead, status_code=201, responses={404: {"description": "Organization or Brand or Process not found"}, 409: {"description": "Duplicate or overlapping requirement"}, 422: {"description": "Process and Brand do not match"}})
def create_requirement(organization_id: OrgId, payload: StaffingRequirementInput, db: DbSession):
    """Create a Brand or Process-wide, weekday, or date-specific staffing minimum."""
    return service.create_requirement(db, organization_id, payload.model_dump())


@router.get("", response_model=list[StaffingRequirementRead], responses={404: {"description": "Organization not found"}, 422: {"description": "Date filters must be supplied as a valid range"}})
def list_requirements(
    organization_id: OrgId,
    db: DbSession,
    brand_id: Annotated[int | None, Query(gt=0)] = None,
    process_id: Annotated[int | None, Query(gt=0)] = None,
    start_date: date | None = None,
    end_date: date | None = None,
):
    if (start_date is None) != (end_date is None):
        raise HTTPException(status_code=422, detail="start_date and end_date must be supplied together")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    return service.list_requirements(db, organization_id, brand_id, process_id, start_date, end_date)


@router.get("/effective", response_model=EffectiveStaffingRequirementRead, responses={404: {"description": "Brand, Process, or effective requirement not found"}, 422: {"description": "Process and Brand do not match"}})
def get_effective_requirement(
    organization_id: OrgId,
    db: DbSession,
    brand_id: Annotated[int, Query(gt=0)],
    requested_date: date,
    process_id: Annotated[int | None, Query(gt=0)] = None,
):
    """Resolve a date's minimum in Process-date, Brand-date, Process-weekday, Brand-weekday, Process-default, Brand-default order."""
    return service.get_effective_requirement(db, organization_id, brand_id, requested_date, process_id)


@router.get("/{requirement_id}", response_model=StaffingRequirementRead, responses={404: {"description": "Staffing requirement not found in this organization"}})
def get_requirement(organization_id: OrgId, requirement_id: RequirementId, db: DbSession):
    return service.get_requirement(db, organization_id, requirement_id)


@router.put("/{requirement_id}", response_model=StaffingRequirementRead, responses={404: {"description": "Staffing requirement, Brand, or Process not found"}, 409: {"description": "Duplicate or overlapping requirement"}, 422: {"description": "Process and Brand do not match"}})
def update_requirement(
    organization_id: OrgId,
    requirement_id: RequirementId,
    payload: StaffingRequirementInput,
    db: DbSession,
):
    return service.update_requirement(db, organization_id, requirement_id, payload.model_dump())


@router.delete("/{requirement_id}", status_code=204, responses={404: {"description": "Staffing requirement not found in this organization"}})
def delete_requirement(organization_id: OrgId, requirement_id: RequirementId, db: DbSession):
    service.delete_requirement(db, organization_id, requirement_id)
    return Response(status_code=204)
