import { expect, test, type Page } from "@playwright/test";
import type { GeneratedRoster } from "@/lib/api";

const apiOrigin = "http://localhost:8000";

function fixtureRoster(year: number, month: number): GeneratedRoster {
  const monthText = String(month).padStart(2, "0");
  const workDate = `${year}-${monthText}-01`;
  const end = new Date(year, month, 0).getDate();
  return {
    success: true,
    generation_run_id: 8,
    roster_id: 41,
    roster_version: 1,
    status: "succeeded",
    solver_status: "OPTIMAL",
    year,
    month,
    pod_id: 3,
    assignments: [{
      assignment_id: 91,
      employee_id: 10,
      employee_name: "Maya Chen",
      process_id: 7,
      process_name: "Customer Care",
      brand_id: 5,
      brand_name: "Northstar",
      process_assignment_id: 110,
      work_date: workDate,
      starts_at: `${workDate}T09:00:00Z`,
      ends_at: `${workDate}T17:00:00Z`,
      is_locked: false,
    }],
    employee_statuses: [10, 11].flatMap((employeeId) => Array.from({ length: end }, (_, index) => ({
      employee_id: employeeId,
      employee_name: employeeId === 10 ? "Maya Chen" : "Leo Park",
      work_date: `${year}-${monthText}-${String(index + 1).padStart(2, "0")}`,
      status: employeeId === 10 && index === 0 ? "working" : "day_off",
    }))),
    coverage: [{ scope: "process", work_date: workDate, brand_id: 5, process_id: 7, sequence: 1, required: 1, scheduled: 1, uncovered: 0 }],
    error: null,
  };
}

async function installApiMocks(page: Page, infeasibleGeneration = false) {
  let lockState = false;
  let storedRoster: GeneratedRoster | null = null;
  await page.route(`${apiOrigin}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    if (path === "/health") return json({ status: "ok" });
    if (path === "/organizations" && method === "GET") return json([{ id: 1, name: "Northstar Operations" }]);
    if (path === "/organizations/1/pods") return json([{ id: 3, organization_id: 1, name: "Customer Operations" }]);
    if (path === "/organizations/1/brands") return json([{ id: 5, organization_id: 1, pod_id: 3, name: "Northstar" }]);
    if (path === "/organizations/1/processes") return json([{ id: 7, organization_id: 1, pod_id: 3, brand_id: 5, name: "Customer Care", timezone: "UTC" }]);
    if (path === "/organizations/1/employees") return json([
      { id: 10, organization_id: 1, pod_id: 3, full_name: "Maya Chen", email: "maya@example.test", role: "Associate", is_active: true },
      { id: 11, organization_id: 1, pod_id: 3, full_name: "Leo Park", email: "leo@example.test", role: "Associate", is_active: true },
    ]);
    if (path === "/organizations/1/rosters/generate" && method === "POST") {
      const body = request.postDataJSON() as { year: number; month: number; preserve_locks_from_roster_id?: number };
      if (lockState && body.preserve_locks_from_roster_id !== 41) {
        return json({ detail: "Locked roster must be supplied during regeneration." }, 409);
      }
      const roster = fixtureRoster(body.year, body.month);
      if (infeasibleGeneration) {
        roster.success = false;
        roster.roster_id = null;
        roster.status = "infeasible";
        roster.error = "Hard staffing requirements cannot be met.";
        roster.assignments = [];
        roster.coverage[0] = { ...roster.coverage[0], scheduled: 0, uncovered: 1 };
        return json(roster, 409);
      }
      roster.assignments[0].is_locked = lockState;
      storedRoster = roster;
      return json(roster, 201);
    }
    if (path === "/organizations/1/rosters/lookup" && method === "GET") {
      if (storedRoster) return json(storedRoster);
      return json({ detail: "No roster exists for this Pod and month." }, 404);
    }
    if (path === "/organizations/1/rosters/41/validate" || path === "/organizations/1/rosters/41/revalidate") {
      return json({ roster_id: 41, valid: true, conflict_count: 0, conflicts: [] });
    }
    if (path === "/organizations/1/processes/7/calendar/effective") return json([]);
    if (path === "/organizations/1/employees/11/process-assignments") return json([
      { id: 111, employee_id: 11, process_id: 7, process_name: "Customer Care", brand_id: 5, brand_name: "Northstar", effective_from: "2026-01-01", effective_to: null },
    ]);
    if (path === "/organizations/1/rosters/41/assignments/91" && method === "PUT") {
      if (storedRoster) {
        const body = request.postDataJSON() as { employee_id: number; process_assignment_id: number };
        storedRoster.assignments[0] = { ...storedRoster.assignments[0], employee_id: body.employee_id,
          employee_name: body.employee_id === 11 ? "Leo Park" : "Maya Chen", process_assignment_id: body.process_assignment_id };
      }
      return json({ assignment_id: 91 });
    }
    if (path === "/organizations/1/rosters/41/assignments/91/lock" && method === "PATCH") {
      lockState = (request.postDataJSON() as { is_locked: boolean }).is_locked;
      if (storedRoster) storedRoster.assignments[0] = { ...storedRoster.assignments[0], is_locked: lockState };
      return json({ assignment_id: 91, roster_id: 41, is_locked: lockState });
    }
    if (path.endsWith("/availability") && method === "POST") return json({ id: 30 }, 201);
    return json({ detail: `Unmocked test API call: ${method} ${path}` }, 404);
  });
}

test("dashboard to generated roster, conflict review, manual edit, and assignment lock", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Good morning, team." })).toBeVisible();
  await expect(page.getByText("2", { exact: true }).first()).toBeVisible();

  await page.getByRole("button", { name: "Monthly roster" }).click();
  await page.getByRole("button", { name: "Generate roster" }).click();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Customer Care.*Northstar/ }).first()).toBeVisible();

  await page.getByRole("button", { name: "Conflicts" }).click();
  await expect(page.getByText("Roster passed independent validation")).toBeVisible();

  await page.getByRole("button", { name: "Monthly roster" }).click();
  await page.getByRole("button", { name: /Customer Care.*Northstar/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("dialog").getByLabel("Employee").selectOption("11");
  await expect(page.getByLabel("Process eligibility")).toHaveValue("111");
  await page.getByRole("button", { name: "Validate & continue" }).click();
  await page.getByRole("button", { name: "Save assignment" }).click();
  await expect(page.getByText("Assignment updated and validated")).toBeVisible();

  await page.getByRole("button", { name: /Customer Care.*Northstar/ }).first().click();
  await page.getByRole("button", { name: "Lock assignment" }).click();
  await expect(page.getByRole("button", { name: "Unlock assignment" })).toBeVisible();
  await page.getByRole("button", { name: "Close", exact: true }).click();
  await page.getByRole("button", { name: "Generate roster" }).click();
  await expect(page.getByText("Monthly roster generated")).toBeVisible();
  await page.getByRole("button", { name: /Customer Care.*Northstar/ }).first().click();
  await expect(page.getByRole("button", { name: "Unlock assignment" })).toBeVisible();
  await page.getByRole("button", { name: "Unlock assignment" }).click();
  await expect(page.getByRole("button", { name: "Lock assignment" })).toBeVisible();
  await page.reload();
  await page.getByRole("button", { name: "Monthly roster" }).click();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Customer Care.*Northstar/ }).first()).toBeVisible();
});

test("empty organization state is honest and can create a workspace", async ({ page }) => {
  let created = false;
  await page.route(`${apiOrigin}/organizations`, async (route) => {
    if (route.request().method() === "GET") return route.fulfill({ json: created ? [{ id: 2, name: "New Workspace" }] : [] });
    created = true;
    return route.fulfill({ status: 201, json: { id: 2, name: "New Workspace" } });
  });
  await page.route(`${apiOrigin}/organizations/2/**`, (route) => route.fulfill({ json: [] }));
  await page.route(`${apiOrigin}/health`, (route) => route.fulfill({ json: { status: "ok" } }));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "A clearer way to plan your people." })).toBeVisible();
  await page.getByLabel("Organization name").fill("New Workspace");
  await page.getByRole("button", { name: "Create workspace" }).click();
  await expect(page.getByRole("heading", { name: "Good morning, team." })).toBeVisible();
});

test("dashboard presents a loading skeleton while workspace data is in flight", async ({ page }) => {
  await page.route(`${apiOrigin}/health`, (route) => route.fulfill({ json: { status: "ok" } }));
  await page.route(`${apiOrigin}/organizations`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 450));
    return route.fulfill({ json: [{ id: 1, name: "Northstar Operations" }] });
  });
  await page.route(`${apiOrigin}/organizations/1/**`, (route) => route.fulfill({ json: [] }));
  await page.goto("/");
  await expect(page.getByLabel("Loading workspace")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Good morning, team." })).toBeVisible();
});

test("backend connection failures show a useful error instead of sample data", async ({ page }) => {
  await page.route(`${apiOrigin}/**`, (route) => route.abort());
  await page.goto("/");
  await expect(page.locator(".error-banner")).toContainText("Can't reach the roster API");
  await expect(page.getByRole("heading", { name: "A clearer way to plan your people." })).toHaveCount(0);
});

test("infeasible generation surfaces uncovered staffing without showing a successful roster", async ({ page }) => {
  await installApiMocks(page, true);
  await page.goto("/");
  await page.getByRole("button", { name: "Monthly roster" }).click();
  await page.getByRole("button", { name: "Generate roster" }).click();
  await expect(page.getByRole("heading", { name: "Conflict management" })).toBeVisible();
  await expect(page.getByText("Generation did not produce a roster")).toBeVisible();
  await expect(page.getByText("1 of 1 required position(s) uncovered.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toHaveCount(0);
});

test("availability changes save through the API and revalidate a loaded roster", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await page.getByRole("button", { name: "Monthly roster" }).click();
  await page.getByRole("button", { name: "Generate roster" }).click();
  await page.getByRole("button", { name: "Availability" }).click();
  await page.getByLabel("Employee").selectOption("10");
  await page.getByLabel("Weekly off day").selectOption("2");
  await page.getByRole("button", { name: "Save change and revalidate" }).click();
  await expect(page.getByText("Availability saved · roster remains valid")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Conflict management" })).toBeVisible();
});
