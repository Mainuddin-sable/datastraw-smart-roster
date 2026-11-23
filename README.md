# Datastraw Smart Roster

Initial application foundation for a roster management system. The repository contains a Next.js App Router frontend and a FastAPI backend. SQLAlchemy 2.x models and Alembic migrations target PostgreSQL through Psycopg and also support a local SQLite development/test database.

## Project layout

```text
app/                             Next.js frontend
backend/app/                     FastAPI routers, services, SQLAlchemy models and Pydantic schemas
backend/alembic/                 schema migrations
backend/tests/                   API, domain constraint and migration tests
docs/database-architecture.md    domain relationships and database decisions
```

## Local setup

1. Copy `.env.example` to the repository root as `.env`. Set `DATABASE_URL` to a Supabase PostgreSQL URI when using the hosted database. Keep populated environment files private.
2. Install frontend dependencies with `npm.cmd install`; start the frontend with `npm.cmd run dev`.
3. With Python 3.11 or newer, install the API with `python -m pip install -e "./backend[dev]"`.
4. From `backend/`, run `alembic upgrade head` and start the API with `uvicorn app.main:app --reload`.
5. Run backend tests from `backend/` with `python -m pytest`. Run frontend checks from the repository root with `npm.cmd run typecheck` and `npm.cmd run build`.

If `DATABASE_URL` is unset, the backend uses `sqlite:///./smart_roster.db` relative to its working directory. SQLite foreign-key enforcement is enabled on application and migration connections. The API liveness endpoint is `GET /health`; it does not claim database readiness. Interactive API documentation is available at `/docs`.

## Organization structure API

The API supports create/list/get/update operations for Organizations, Pods, Brands, and Processes. Child resource routes are scoped under `/organizations/{organization_id}` so related lookups and writes verify organization ownership. Create requests return `201`; reads and updates return `200`; missing or cross-organization resources return `404`; duplicate Pod, Brand, or Process names return `409`; invalid request data returns `422`.

Authentication and authorization are not implemented yet. These endpoints are for development use only and must not be exposed to untrusted clients until access control is added.

Process operating calendars, organization holidays, date and weekday overrides, and Brand/Process minimum staffing requirements are also exposed through FastAPI. Calendar effective-day reads apply date overrides, holiday behavior, weekday overrides, and recurring rules in that order; holidays close Processes by default. Public holiday data is supplied by API clients and is not bundled into the application.

Monthly roster generation is available at `POST /organizations/{organization_id}/rosters/generate`. It uses OR-Tools CP-SAT, requires a Pod and month, enforces process and brand staffing minima, employee process eligibility, explicit availability closures, approved leave, and non-overlapping work blocks. The request has a bounded solver time limit; infeasible runs are recorded and return uncovered staffing demands without saving a Roster.

Saved rosters can be independently checked with `POST /organizations/{organization_id}/rosters/{roster_id}/validate` or `/revalidate` after availability or leave changes. Assignments can be edited with `PUT .../{roster_id}/assignments/{assignment_id}` and locked/unlocked with `PATCH .../{assignment_id}/lock`. To regenerate a month while preserving locked assignments, pass `preserve_locks_from_roster_id` to the generation endpoint. An invalid lock blocks regeneration and is never silently dropped. Authentication is not implemented; these endpoints remain development-stage APIs.

## Database foundation

An Organization owns Pods. A Pod owns its Brands and Employees; each Process belongs to one Brand in the same Pod. Employees can have multiple effective-dated Process Assignments, including assignments across several brands and processes. Operating rules and their day/date/holiday overrides belong to Processes. Employee recurring availability, leave, comp-off grants, and preferences are separate employee records.

A Roster covers a Pod and a date period. It has one Roster Entry per employee and date, and an entry can contain multiple Roster Work Assignments linked to the employee's Process Assignments. Generation Runs record generation lifecycle and summaries; Audit Logs record actor and entity events. Database ownership constraints prevent cross-organization and cross-Pod references where the relationship is tenant-scoped.

The full entity map, effective-date conventions, unique constraints, and known validation boundaries are in [docs/database-architecture.md](docs/database-architecture.md). The existing organization/shift assignment tables remain for compatibility. Migration `0002` backfills one `Default` Pod for each existing Organization and adds tenant-safe ownership columns to legacy assignments.

Organization structure CRUD is implemented. The API does not yet expose CRUD for roster, employee-availability, generation-run, or audit resources, and it does not implement roster generation/scheduling. PostgreSQL is the deployment target, but no PostgreSQL integration run has been performed for this foundation.
