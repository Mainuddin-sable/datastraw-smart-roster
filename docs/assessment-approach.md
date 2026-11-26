# DataStraw Internship Assessment Approach

## Architecture

The system separates Organizations, Pods, Brands, Processes, Employees, effective-dated process eligibility, calendars, staffing requirements, availability, leave requests, and versioned rosters. Composite foreign keys and service checks keep relationships within the same organization and Pod. SQLite is the selected assessment database. The assessment seed is intended for a separate empty database, creates 30 employees across 3 Pods with 23 Brands and 28 Processes, and deliberately leaves Pod Lead choices to the reviewer.

## Scheduling and validation

Monthly generation builds dated staffing demand from effective process and brand calendars, holidays, overrides, and minimum staffing rules. OR-Tools CP-SAT applies eligibility, availability, approved leave/comp-off, interval overlap, and locked assignments. A separate validator checks generated and edited rosters, including full operating-interval coverage. Infeasible generation and uncovered demand are reported instead of being presented as a successful roster. Planners can review conflicts, request eligible replacement candidates, manually edit or lock assignments, and revalidate.

## Tradeoffs and limitations

The implementation favors explicit hard constraints and explainable coverage failures over silently relaxing requirements. Demo authentication is shared HTTP Basic access, not individual accounts or role-based authorization. Earned-hour comp-off grants are not connected to dated comp-off requests or balance accounting. The solver is bounded, but performance at twice the sample size has not been load-tested. Railway frontend and backend health endpoints respond, but the database-backed API currently fails because the deployed SQLite database lacks the `organizations` table. The live roster workflow, persistence, and deployed authentication behavior remain unverified; repair the volume only after read-only inspection, backup, and approval.
