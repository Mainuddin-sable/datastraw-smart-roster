# Deployment Readiness (SQLite)

## Current status

The application database for this assessment is SQLite. Railway currently serves the public frontend and backend health endpoint, but a database-backed organization request fails because the deployed SQLite database has no `organizations` table. The live roster workflow and persistence are not verified, and the deployed commit and API authentication behavior must be confirmed before public review. The pushed `main` branch does not yet include all local assessment changes. There is no deployment manifest in the repository; Railway service settings are external to this checkout. The frontend is a Next.js application and the API is a separate FastAPI service.

All organization routes now require shared HTTP Basic credentials configured with server-side `DEMO_AUTH_USERNAME` and `DEMO_AUTH_PASSWORD`. Production startup fails when either is missing. `/health` is public; `/docs`, `/redoc`, and `/openapi.json` are disabled when `APP_ENV=production`. CORS only controls browser cross-origin access; authentication remains enforced for direct API clients. Use HTTPS and never place these credentials in `NEXT_PUBLIC_*` variables.

## Database path and persistence

SQLite stores data in a file on the backend host. The file must be on a persistent volume or durable filesystem that survives process restarts, redeployments, and host replacement. A container's ordinary writable layer or an ephemeral filesystem is not sufficient.

Set `DATABASE_URL` to an absolute SQLite path on the host's persistent mount. For example, if the host mounts persistent storage at `/var/data`, use:

```text
sqlite:////var/data/smart_roster.db
```

The four slashes represent an absolute Unix path. Use the actual mount path supplied by the chosen host. Do not copy this example unless `/var/data` is that host's persistent mount. The local URL `sqlite:///./smart_roster.db` is relative to the API process's working directory; the documented local workflow starts the API from `backend/`, resolving to `backend/smart_roster.db`.

The existing `backend/smart_roster.db` is ignored by Git and is not included in a source checkout. A deployment therefore needs its own intentionally initialized database on persistent storage. Do not point a deployment at a valuable local database or run seed scripts against a populated database. Back up the SQLite file using a method supported by the host before schema changes or data maintenance.

## Service configuration

The frontend and backend can be hosted separately. Configure these environment variables in the respective services:

| Service | Variable | Value |
|---|---|---|
| Backend | `DATABASE_URL` | Absolute SQLite URL on persistent storage |
| Backend | `CORS_ORIGINS` | Exact deployed frontend origin, such as `https://roster.example.com` |
| Backend | `DEMO_AUTH_USERNAME` | Private demo login username |
| Backend | `DEMO_AUTH_PASSWORD` | Strong private demo login password |
| Frontend | `NEXT_PUBLIC_API_URL` | Public URL of the FastAPI service, set before the frontend build |

`NEXT_PUBLIC_API_URL` is embedded in the built frontend. Rebuild after changing it. For browser requests carrying the `Authorization` header, configure the backend for the exact frontend origin; do not use `*`. Confirm CORS preflight requests work. Keep private environment files out of Git.

## ₹0 provider constraints and current Railway status

The public Railway frontend responds successfully, and the backend `/health` endpoint responds successfully. A database-backed organization API request currently fails because the deployed SQLite database has no `organizations` table. Full application functionality, including database-backed workflows and persistence after restart, is not yet verified. Do not describe the application as fully working or submission-ready online until those checks pass.

The backend is configured to use SQLite on the Railway `/data` volume. The live database must be inspected read-only and backed up before any migration or seed operation; no database initialization is performed by this documentation change. Whether the current Railway arrangement satisfies the ₹0 budget without payment details has not been verified. Do not use ephemeral storage for SQLite or switch to PostgreSQL/Supabase as a workaround; SQLite remains the assessment database.

## Pre-submission deployment checks

1. Confirm the chosen service's plan is ₹0 without a paid trial or payment details, and verify its persistent-storage and ASGI terms.
2. Configure the backend with the host's absolute persistent SQLite path and the frontend's exact origin. Configure the frontend API URL before building.
3. Configure strong demo credentials and `APP_ENV=production`. Verify unauthenticated and invalid-credential requests receive `401`, valid credentials allow the expected workflow, and only `/health` remains public. Use HTTPS. Shared demo credentials provide no individual identity, role distinction, or per-user audit trail; rotate them if disclosed.
4. Initialize only the deployment's intended database. Verify schema migration, expected seed distribution, database file location, and backup/restore behavior. Never run the assessment seeder against a populated database.
5. Verify health, database-backed reads and writes, roster generation, lookup, manual edit, validation, browser CORS behavior, and persistence after a restart/redeployment.
6. Record the deployed URL and test evidence. The `/health` endpoint is liveness-only and is not sufficient as a deployment verification.

Until these checks pass, report that the frontend and health endpoint are live but the full application is not verified; do not describe the roster application as submission-ready online.
