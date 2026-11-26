from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import brands, calendars, employees, leave_requests, organizations, pods, processes, rosters, staffing
from app.api.security import require_demo_auth
from app.core.config import get_settings
from app.services.employees import InvalidRelationship
from app.services.calendars import InvalidCalendarInput
from app.services.organization_structure import ResourceConflict, ResourceNotFound

settings = get_settings()


def api_docs_urls(app_env: str) -> dict[str, str | None]:
    if app_env.casefold() == "production":
        return {"docs_url": None, "redoc_url": None, "openapi_url": None}
    return {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"}


app = FastAPI(
    title="Datastraw Smart Roster API",
    version="0.2.0",
    **api_docs_urls(settings.app_env),
    description=(
        "Organization and roster management APIs. Organization routes require the configured "
        "shared HTTP Basic demo credentials."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Authorization", "Content-Type"],
)


@app.exception_handler(ResourceNotFound)
async def resource_not_found_handler(request: Request, exc: ResourceNotFound) -> JSONResponse:
    return JSONResponse(status_code=404, content={"detail": str(exc)})


@app.exception_handler(ResourceConflict)
async def resource_conflict_handler(request: Request, exc: ResourceConflict) -> JSONResponse:
    return JSONResponse(status_code=409, content={"detail": str(exc)})


@app.exception_handler(InvalidRelationship)
async def invalid_relationship_handler(request: Request, exc: InvalidRelationship) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(InvalidCalendarInput)
async def invalid_calendar_input_handler(request: Request, exc: InvalidCalendarInput) -> JSONResponse:
    return JSONResponse(status_code=422, content={"detail": str(exc)})


protected_routes = {"dependencies": [Depends(require_demo_auth)]}
app.include_router(organizations.router, **protected_routes)
app.include_router(pods.router, **protected_routes)
app.include_router(brands.router, **protected_routes)
app.include_router(processes.router, **protected_routes)
app.include_router(employees.router, **protected_routes)
app.include_router(leave_requests.router, **protected_routes)
app.include_router(calendars.router, **protected_routes)
app.include_router(staffing.router, **protected_routes)
app.include_router(rosters.router, **protected_routes)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Process liveness; does not require a configured database."""
    return {"status": "ok"}
