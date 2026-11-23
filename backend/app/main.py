from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routers import brands, employees, organizations, pods, processes
from app.core.config import get_settings
from app.services.employees import InvalidRelationship
from app.services.organization_structure import ResourceConflict, ResourceNotFound

settings = get_settings()
app = FastAPI(
    title="Datastraw Smart Roster API",
    version="0.2.0",
    description=(
        "Organization structure management APIs. Authentication and authorization are not yet "
        "implemented; this API is currently intended for development use only."
    ),
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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


app.include_router(organizations.router)
app.include_router(pods.router)
app.include_router(brands.router)
app.include_router(processes.router)
app.include_router(employees.router)


@app.get("/health", tags=["health"])
def health_check() -> dict[str, str]:
    """Process liveness; does not require a configured database."""
    return {"status": "ok"}
