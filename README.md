# Datastraw Smart Roster

Initial application foundation for a roster management system. The repository contains a Next.js App Router frontend and a FastAPI backend. SQLAlchemy 2.x models and Alembic migrations target PostgreSQL through Psycopg and also support a local SQLite development/test database.

## Project layout

```text
app/                              Next.js frontend
backend/app/                     FastAPI, SQLAlchemy models and Pydantic read schemas
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

## Database foundation

An Organization owns Pods. A Pod owns its Brands and Employees; each Process belongs to one Brand in the same Pod. Employees can have multiple effective-dated Process Assignments, including assignments across several brands and processes. Operating rules and their day/date/holiday overrides belong to Processes. Employee recurring availability, leave, comp-off grants, and preferences are separate employee records.

A Roster covers a Pod and a date period. It has one Roster Entry per employee and date, and an entry can contain multiple Roster Work Assignments linked to the employee's Process Assignments. Generation Runs record generation lifecycle and summaries; Audit Logs record actor and entity events. Database ownership constraints prevent cross-organization and cross-Pod references where the relationship is tenant-scoped.

The full entity map, effective-date conventions, unique constraints, and known validation boundaries are in [docs/database-architecture.md](docs/database-architecture.md). The existing organization/shift assignment tables remain for compatibility. Migration `0002` backfills one `Default` Pod for each existing Organization and adds tenant-safe ownership columns to legacy assignments.

The API currently provides liveness and Pydantic ORM read contracts; it does not expose CRUD endpoints or implement roster generation/scheduling. PostgreSQL is the deployment target, but no PostgreSQL integration run has been performed for this foundation.
