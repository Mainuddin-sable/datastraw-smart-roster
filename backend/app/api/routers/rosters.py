from typing import Annotated

from fastapi import APIRouter, Depends, Path, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.schemas import GenerateMonthlyRosterRequest, MonthlyRosterGenerationRead
from app.services import roster_generation as service

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
    result = service.generate_monthly_roster(
        db,
        organization_id=organization_id,
        pod_id=payload.pod_id,
        year=payload.year,
        month=payload.month,
        process_id=payload.process_id,
        time_limit_seconds=payload.solver_time_limit_seconds,
    )
    if result["success"]:
        return result
    status_code = {
        "infeasible": status.HTTP_409_CONFLICT,
        "unknown": status.HTTP_503_SERVICE_UNAVAILABLE,
    }.get(result["status"], 422)
    return JSONResponse(status_code=status_code, content=jsonable_encoder(result))
