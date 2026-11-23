from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import PodCreate, PodRead, PodUpdate
from app.services import organization_structure as service

router = APIRouter(prefix="/organizations/{organization_id}/pods", tags=["pods"])
DbSession = Annotated[Session, Depends(get_db)]


@router.post("", response_model=PodRead, status_code=status.HTTP_201_CREATED, responses={404: {"description": "Organization not found"}, 409: {"description": "Duplicate pod name"}})
def create_pod(organization_id: int, payload: PodCreate, db: DbSession):
    """Create a pod owned by the path organization."""
    return service.create_pod(db, organization_id, payload.name)


@router.get("", response_model=list[PodRead], responses={404: {"description": "Organization not found"}})
def list_pods(organization_id: int, db: DbSession):
    """List pods owned by the path organization."""
    return service.list_pods(db, organization_id)


@router.get("/{pod_id}", response_model=PodRead, responses={404: {"description": "Pod not found in this organization"}})
def get_pod(organization_id: int, pod_id: int, db: DbSession):
    """Get a pod within the path organization."""
    return service.get_pod(db, organization_id, pod_id)


@router.put("/{pod_id}", response_model=PodRead, responses={404: {"description": "Pod not found in this organization"}, 409: {"description": "Duplicate pod name"}})
def update_pod(organization_id: int, pod_id: int, payload: PodUpdate, db: DbSession):
    """Replace a pod's editable fields."""
    return service.update_pod(db, organization_id, pod_id, payload.name)
