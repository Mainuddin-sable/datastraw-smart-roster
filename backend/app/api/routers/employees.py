from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import (
    EmployeeAvailabilityCreate,
    EmployeeAvailabilityRead,
    EmployeeAvailabilityUpdate,
    EmployeeCreate,
    EmployeeEligibleProcessRead,
    EmployeeRead,
    EmployeeUpdate,
    ProcessAssignmentCreate,
    ProcessAssignmentDeactivate,
    ProcessAssignmentRead,
    ProcessAssignmentUpdate,
)
from app.services import employees as service

router = APIRouter(prefix="/organizations/{organization_id}/employees", tags=["employees"])
DbSession = Annotated[Session, Depends(get_db)]
OrgId = Annotated[int, Path(gt=0)]
EmployeeId = Annotated[int, Path(gt=0)]
AssignmentId = Annotated[int, Path(gt=0)]
AvailabilityId = Annotated[int, Path(gt=0)]


@router.post("", response_model=EmployeeRead, status_code=status.HTTP_201_CREATED, responses={404: {"description": "Organization or pod not found"}, 409: {"description": "Duplicate email"}})
def create_employee(organization_id: OrgId, payload: EmployeeCreate, db: DbSession):
    """Create an employee in an organization-owned pod."""
    return service.create_employee(
        db, organization_id, payload.pod_id, payload.full_name, payload.email,
        payload.role, payload.is_active,
    )


@router.get("", response_model=list[EmployeeRead], responses={404: {"description": "Organization not found"}})
def list_employees(
    organization_id: OrgId,
    db: DbSession,
    is_active: Annotated[bool | None, Query()] = None,
):
    """List employees; optionally filter by active status."""
    return service.list_employees(db, organization_id, is_active)


@router.get("/{employee_id}", response_model=EmployeeRead, responses={404: {"description": "Employee not found in this organization"}})
def get_employee(organization_id: OrgId, employee_id: EmployeeId, db: DbSession):
    """Get an employee within the path organization."""
    return service.get_employee(db, organization_id, employee_id)


@router.put("/{employee_id}", response_model=EmployeeRead, responses={404: {"description": "Employee not found in this organization"}, 409: {"description": "Duplicate email"}})
def update_employee(organization_id: OrgId, employee_id: EmployeeId, payload: EmployeeUpdate, db: DbSession):
    """Replace employee details and active status. Pod ownership is fixed at creation."""
    return service.update_employee(
        db, organization_id, employee_id, payload.full_name, payload.email,
        payload.role, payload.is_active,
    )


@router.post("/{employee_id}/process-assignments", response_model=ProcessAssignmentRead, status_code=status.HTTP_201_CREATED, responses={404: {"description": "Employee or process not found in this organization"}, 409: {"description": "Overlapping assignment"}, 422: {"description": "Process belongs to another pod"}})
def create_process_assignment(
    organization_id: OrgId, employee_id: EmployeeId, payload: ProcessAssignmentCreate, db: DbSession
):
    """Assign an employee to a process for an effective date window."""
    return service.create_process_assignment(
        db, organization_id, employee_id, payload.process_id,
        payload.effective_from, payload.effective_to,
    )


@router.get("/{employee_id}/process-assignments", response_model=list[EmployeeEligibleProcessRead], responses={404: {"description": "Employee not found in this organization"}})
def list_process_assignments(organization_id: OrgId, employee_id: EmployeeId, db: DbSession):
    """List process eligibility periods, including each process's Brand."""
    return service.list_process_assignments(db, organization_id, employee_id)


@router.put("/{employee_id}/process-assignments/{assignment_id}", response_model=ProcessAssignmentRead, responses={404: {"description": "Employee, assignment, or process not found in this organization"}, 409: {"description": "Overlapping assignment"}, 422: {"description": "Process belongs to another pod or invalid effective date"}})
def update_process_assignment(
    organization_id: OrgId,
    employee_id: EmployeeId,
    assignment_id: AssignmentId,
    payload: ProcessAssignmentUpdate,
    db: DbSession,
):
    """Replace process eligibility and its effective date window."""
    return service.update_process_assignment(
        db, organization_id, employee_id, assignment_id, payload.process_id,
        payload.effective_from, payload.effective_to,
    )


@router.post("/{employee_id}/process-assignments/{assignment_id}/deactivate", response_model=ProcessAssignmentRead, responses={404: {"description": "Employee or assignment not found in this organization"}, 409: {"description": "Overlapping assignment"}, 422: {"description": "End date precedes start date"}})
def deactivate_process_assignment(
    organization_id: OrgId,
    employee_id: EmployeeId,
    assignment_id: AssignmentId,
    payload: ProcessAssignmentDeactivate,
    db: DbSession,
):
    """End eligibility on the supplied inclusive date."""
    return service.deactivate_process_assignment(
        db, organization_id, employee_id, assignment_id, payload.effective_to
    )


@router.get("/{employee_id}/availability", response_model=list[EmployeeAvailabilityRead], responses={404: {"description": "Employee not found in this organization"}, 422: {"description": "Invalid date range"}})
def list_availability(
    organization_id: OrgId,
    employee_id: EmployeeId,
    db: DbSession,
    start_date: Annotated[date, Query(description="First date in the requested inclusive range")],
    end_date: Annotated[date, Query(description="Last date in the requested inclusive range")],
):
    """Return recurring weekday rules effective during any part of the date range."""
    if end_date < start_date:
        raise HTTPException(status_code=422, detail="end_date must be on or after start_date")
    return service.list_availability(db, organization_id, employee_id, start_date, end_date)


@router.post("/{employee_id}/availability", response_model=EmployeeAvailabilityRead, status_code=status.HTTP_201_CREATED, responses={404: {"description": "Employee not found in this organization"}, 409: {"description": "Overlapping availability period"}})
def create_availability(
    organization_id: OrgId,
    employee_id: EmployeeId,
    payload: EmployeeAvailabilityCreate,
    db: DbSession,
):
    """Create a recurring weekday availability rule."""
    return service.create_availability(db, organization_id, employee_id, payload.model_dump())


@router.put("/{employee_id}/availability/{availability_id}", response_model=EmployeeAvailabilityRead, responses={404: {"description": "Employee or availability not found in this organization"}, 409: {"description": "Overlapping availability period"}})
def update_availability(
    organization_id: OrgId,
    employee_id: EmployeeId,
    availability_id: AvailabilityId,
    payload: EmployeeAvailabilityUpdate,
    db: DbSession,
):
    """Replace a recurring availability rule and its effective date window."""
    return service.update_availability(
        db, organization_id, employee_id, availability_id, payload.model_dump()
    )
