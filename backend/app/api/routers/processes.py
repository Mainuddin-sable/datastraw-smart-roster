from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import ProcessCreate, ProcessRead, ProcessUpdate
from app.services import organization_structure as service

router = APIRouter(prefix="/organizations/{organization_id}/processes", tags=["processes"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=ProcessRead, status_code=status.HTTP_201_CREATED, responses={404: {"description": "Organization or brand not found"}, 409: {"description": "Duplicate process name"}})
def create_process(organization_id: int, payload: ProcessCreate, db: DbSession):
    """Create a process under a brand owned by the path organization."""
    return service.create_process(db, organization_id, payload.brand_id, payload.name, payload.timezone)


@router.get("", response_model=list[ProcessRead], responses={404: {"description": "Organization not found"}})
def list_processes(organization_id: int, db: DbSession):
    """List processes owned by the path organization."""
    return service.list_processes(db, organization_id)


@router.get("/{process_id}", response_model=ProcessRead, responses={404: {"description": "Process not found in this organization"}})
def get_process(organization_id: int, process_id: int, db: DbSession):
    """Get a process within the path organization."""
    return service.get_process(db, organization_id, process_id)


@router.put("/{process_id}", response_model=ProcessRead, responses={404: {"description": "Process or brand not found in this organization"}, 409: {"description": "Duplicate process name"}})
def update_process(organization_id: int, process_id: int, payload: ProcessUpdate, db: DbSession):
    """Replace a process's brand, name, and timezone within the same organization."""
    return service.update_process(
        db, organization_id, process_id, payload.brand_id, payload.name, payload.timezone
    )
