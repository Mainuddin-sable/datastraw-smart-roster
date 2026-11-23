from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import OrganizationCreate, OrganizationRead, OrganizationUpdate
from app.services import organization_structure as service

router = APIRouter(prefix="/organizations", tags=["organizations"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=OrganizationRead, status_code=status.HTTP_201_CREATED)
def create_organization(payload: OrganizationCreate, db: DbSession):
    """Create an organization."""
    return service.create_organization(db, payload.name)


@router.get("", response_model=list[OrganizationRead])
def list_organizations(db: DbSession):
    """List organizations in ID order."""
    return service.list_organizations(db)


@router.get("/{organization_id}", response_model=OrganizationRead, responses={404: {"description": "Organization not found"}})
def get_organization(organization_id: int, db: DbSession):
    """Get one organization by ID."""
    return service.get_organization(db, organization_id)


@router.put("/{organization_id}", response_model=OrganizationRead, responses={404: {"description": "Organization not found"}})
def update_organization(organization_id: int, payload: OrganizationUpdate, db: DbSession):
    """Replace an organization's editable fields."""
    return service.update_organization(db, organization_id, payload.name)
