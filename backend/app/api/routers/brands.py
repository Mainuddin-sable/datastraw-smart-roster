from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import BrandCreate, BrandRead, BrandUpdate
from app.services import organization_structure as service

router = APIRouter(prefix="/organizations/{organization_id}/brands", tags=["brands"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=BrandRead, status_code=status.HTTP_201_CREATED, responses={404: {"description": "Organization or pod not found"}, 409: {"description": "Duplicate brand name"}})
def create_brand(organization_id: int, payload: BrandCreate, db: DbSession):
    """Create a brand under a pod owned by the path organization."""
    return service.create_brand(db, organization_id, payload.pod_id, payload.name)


@router.get("", response_model=list[BrandRead], responses={404: {"description": "Organization not found"}})
def list_brands(organization_id: int, db: DbSession):
    """List brands owned by the path organization."""
    return service.list_brands(db, organization_id)


@router.get("/{brand_id}", response_model=BrandRead, responses={404: {"description": "Brand not found in this organization"}})
def get_brand(organization_id: int, brand_id: int, db: DbSession):
    """Get a brand within the path organization."""
    return service.get_brand(db, organization_id, brand_id)


@router.put("/{brand_id}", response_model=BrandRead, responses={404: {"description": "Brand or pod not found in this organization"}, 409: {"description": "Duplicate brand name"}})
def update_brand(organization_id: int, brand_id: int, payload: BrandUpdate, db: DbSession):
    """Replace a brand's pod and name within the same organization."""
    return service.update_brand(db, organization_id, brand_id, payload.pod_id, payload.name)
