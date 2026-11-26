# Datastraw Smart Roster

Roster management system with a Next.js App Router frontend and a FastAPI backend. SQLAlchemy 2.x and Alembic use SQLite as the application database.

## Project layout

```text
app/                             Next.js frontend
backend/app/                     FastAPI routers, services, SQLAlchemy models and Pydantic schemas
backend/alembic/                 schema migrations
backend/tests/                   API, domain constraint and migration tests
docs/database-architecture.md    domain relationships and database decisions
```

## Local setup

1. Copy `.env.example` to the repository root as `.env`. Set unique `DEMO_AUTH_USERNAME` and `DEMO_AUTH_PASSWORD` values for the API; keep populated environment files private. SQLite is the default. The documented local workflow runs the API from `backend/`, so `sqlite:///./smart_roster.db` resolves to `backend/smart_roster.db`.
2. Install frontend dependencies with `npm.cmd install`; start the frontend with `npm.cmd run dev`.
3. With Python 3.11 or newer, install the API with `python -m pip install -e "./backend[dev]"`.
4. From `backend/`, run `alembic upgrade head` and start the API with `uvicorn app.main:app --reload`.
5. Run backend tests from `backend/` with `python -m pytest`. Run frontend checks from the repository root with `npm.cmd run typecheck`, `npm.cmd run lint`, `npm.cmd run test:e2e`, and `npm.cmd run build`.

If `DATABASE_URL` is unset, the backend uses `sqlite:///./smart_roster.db` relative to its working directory. For a hosted backend, set `DATABASE_URL` to an absolute SQLite file path on storage that persists across restarts and deployments. For example, on Linux a mounted persistent directory at `/var/data` uses `sqlite:////var/data/smart_roster.db`. Confirm the host's mount path and SQLite URL format before deployment. SQLite foreign-key enforcement is enabled on application and migration connections. The API liveness endpoint is `GET /health`; it does not claim database readiness. Interactive API documentation is available at `/docs`.

### Demo data

After creating an organization named `DataStraw Operations` through `POST /organizations`, run `python -m scripts.seed_demo_data` from `backend/`. The script is additive: it retains existing records and fills missing demo resources. Do not use it as a cleanup or repair operation against a populated organization. The current development database contains manually created assessment data, so the seeder has not been rerun against it as part of this work.

For an assessment-shaped demo database, configure a **separate empty** `DATABASE_URL`, run `alembic upgrade head` and then `python -m scripts.seed_assessment_demo` from `backend/`. That seeder refuses to run if the database already has an Organization. It creates 30 employees, 3 Pods, 23 brands (18 single-owner and 5 shared by 2–3 employees), 28 processes, varied operating patterns, a holiday, and staffing minima including a two-person demand. It leaves Pod Leads unassigned for a person to choose. Do not point this profile at the existing `smart_roster.db`.

## Organization structure API

The API supports create/list/get/update operations for Organizations, Pods, Brands, and Processes. Child resource routes are scoped under `/organizations/{organization_id}` so related lookups and writes verify organization ownership. Create requests return `201`; reads and updates return `200`; missing or cross-organization resources return `404`; duplicate Pod, Brand, or Process names return `409`; invalid request data returns `422`.

All organization API routes require the configured shared HTTP Basic demo credentials. Set `DEMO_AUTH_USERNAME` and `DEMO_AUTH_PASSWORD` in the backend environment; production startup fails if either is missing. The frontend asks for the credentials and keeps them in memory only. Use HTTPS and do not expose the API without configuring a strong, private password.

Process operating calendars, organization holidays, date and weekday overrides, and Brand/Process minimum staffing requirements are also exposed through FastAPI. Calendar effective-day reads apply date overrides, holiday behavior, weekday overrides, and recurring rules in that order; holidays close Processes by default. Public holiday data is supplied by API clients and is not bundled into the application.

Monthly roster generation is available at `POST /organizations/{organization_id}/rosters/generate`. It uses OR-Tools CP-SAT, requires a Pod and month, enforces process and brand staffing minima, employee process eligibility, explicit availability closures, approved leave, and non-overlapping work blocks. The request has a bounded solver time limit; infeasible runs are recorded and return uncovered staffing demands without saving a Roster.

The latest saved roster for a Pod/month can be retrieved with `GET /organizations/{organization_id}/rosters/lookup?pod_id=...&year=...&month=...`. It returns persisted assignment IDs, employee-day statuses, lock state, and stored generation coverage.

Saved rosters can be independently checked with `POST /organizations/{organization_id}/rosters/{roster_id}/validate` or `/revalidate` after availability or leave changes. Assignments can be edited with `PUT .../{roster_id}/assignments/{assignment_id}` and locked/unlocked with `PATCH .../{assignment_id}/lock`. To regenerate a month while preserving locked assignments, pass `preserve_locks_from_roster_id` to the generation endpoint. An invalid lock blocks regeneration and is never silently dropped. These routes require the same HTTP Basic credentials as the rest of the organization API.

## Database foundation

An Organization owns Pods. A Pod owns its Brands and Employees; each Process belongs to one Brand in the same Pod. Employees can have multiple effective-dated Process Assignments, including assignments across several brands and processes. Operating rules and their day/date/holiday overrides belong to Processes. Employee recurring availability, leave, comp-off grants, and preferences are separate employee records.

A Roster covers a Pod and a date period. It has one Roster Entry per employee and date, and an entry can contain multiple Roster Work Assignments linked to the employee's Process Assignments. Generation Runs record generation lifecycle and summaries; Audit Logs record actor and entity events. Database ownership constraints prevent cross-organization and cross-Pod references where the relationship is tenant-scoped.

The full entity map, effective-date conventions, unique constraints, and known validation boundaries are in [docs/database-architecture.md](docs/database-architecture.md). The existing organization/shift assignment tables remain for compatibility. Migration `0002` backfills one `Default` Pod for each existing Organization and adds tenant-safe ownership columns to legacy assignments.

Organization structure, employee, calendar, staffing, availability, leave/comp-off requests, roster generation, validation, manual assignment editing, and lock APIs are implemented. Approved leave and approved comp-off requests block roster assignments. Earned-hour comp-off grants are not connected to dated requests or balance accounting. Organization API routes now require shared HTTP Basic credentials; this demo account has no individual roles or user-level audit trail. SQLite is the chosen application database. Current Railway status is described in the Submission status section below.

## Assessment questions

1. **How is the organization represented?** An Organization owns Pods; Pods own Employees and Brands; each Brand owns Processes. Effective-dated Process Assignments link employees to the processes they can perform. Tenant-scoped foreign keys and service checks reject cross-organization relationships. A Pod can designate one active employee as its lead.
2. **How is a monthly roster generated?** The API builds dated staffing demands from process/brand calendars, effective staffing rules, holidays and overrides, then solves them with OR-Tools CP-SAT. Employee eligibility, recurring availability, approved time off, assignment locks, and overlapping intervals constrain candidates. Infeasible schedules return diagnosed coverage gaps and are not saved as successful rosters.
3. **How are single-person and multi-person brands handled?** Each process has its own effective staffing requirement. One employee may be eligible for all processes in a brand; multiple employees can be assigned to different or shared processes. The assessment seed has 18 single-owner brands and 5 brands with 2–3 employees, including two cross-brand process assignments. The optimizer schedules each process demand independently and the validator checks both process and brand coverage. The older additive development seeder remains a broad cross-training example.
4. **What happens if the employee covering a brand is unavailable?** Availability changes and approved leave are included when independently validating a saved roster. A missed shift becomes a hard coverage gap; the report lists eligible, available replacements when they exist. The roster must be regenerated or manually edited and revalidated to resolve the gap.
5. **What happens when requirements conflict or staffing is insufficient?** Generation reports the solver status and uncovered demand and does not claim success. Independent validation reports hard conflicts such as coverage gaps, unavailability, leave, and overlapping assignments. Preferences are distinct from mandatory constraints and do not make an invalid schedule valid.
6. **What should be automated and what remains under human control?** Calendar and availability constraints, eligibility, minimum coverage, conflict checks, and initial monthly assignments are automated. A planner reviews conflicts, can edit or lock assignments, and must revalidate changes. The optimizer does not silently waive a mandatory rule.
7. **How should the system scale to twice as many employees and brands?** Keep the same tenant-scoped domain model and effective-dated rules, while measuring solver time and database/API latency at the larger workload. Generation is partitioned by Pod and solver time is bounded. SQLite is retained for this assessment; larger concurrent workloads may require a database architecture change, which is outside the current scope.
8. **What would be improved with one more week?** Verify the assessment seed on a reliable persistent-storage host within the ₹0 budget, complete a protected reviewer deployment if a suitable host is available, and load-test the doubled organization size. Add earned-hour comp-off balance accounting only if the assessment expects it beyond approved comp-off scheduling inputs.

## Assessment approach note

This solution models staffing as effective-dated eligibility and demand, then generates a monthly schedule with a constraint solver. Mandatory coverage, calendars, availability and approved leave remain hard rules; planners retain control through manual edits, locks and independent revalidation. See the [assessment approach note](docs/assessment-approach.md) and [database architecture note](docs/database-architecture.md) for the implementation rationale and schema constraints.

## Submission status

Railway currently serves the public frontend and backend health endpoint, but a database-backed organization request fails because the deployed SQLite database has no `organizations` table. The live roster workflow and persistence are therefore not verified, and the deployed commit and API authentication behavior must be confirmed before public review. The pushed `main` branch does not yet include all local assessment changes. Do not seed or migrate the Railway volume until it has been inspected read-only, backed up, and its state reviewed. Under the current ₹0/no-payment-details constraint, do not move SQLite to an ephemeral filesystem. Once the deployment is repaired, configure its absolute SQLite path, strong server-side HTTP Basic credentials, exact HTTPS CORS origin, `APP_ENV=production`, and `NEXT_PUBLIC_API_URL` before the frontend build; verify persistence and a real roster workflow. Record a 3–5 minute demo using the (docs/demo-video-plan.md)](https://drive.google.com/drive/folders/18ZYsXldI_H4OSzAp3i_WdLjGZXTvvwcU?usp=drive_link). Submit the repository, this README, the [approach note](docs/assessment-approach.md), demo link, LinkedIn profile and assessment email as requested by the assessment. Never expose the API over plain HTTP or without the server-side credentials.

See [deployment instructions and the access-control gate](docs/deployment.md). The API uses shared demo HTTP Basic credentials; the application has no role-specific reviewer accounts.
