import { expect, test, type Page } from "@playwright/test";
import { randomBytes } from "node:crypto";
import type { GeneratedRoster } from "@/lib/api";

const apiOrigin = "http://localhost:8000";
const demoCredentials = { username: randomBytes(18).toString("hex"), password: randomBytes(32).toString("base64url") };
const basicHeader = `Basic ${Buffer.from(`${demoCredentials.username}:${demoCredentials.password}`).toString("base64")}`;

async function signIn(page: Page) {
  await page.getByLabel("Username").fill(demoCredentials.username);
  await page.getByLabel("Password").fill(demoCredentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByRole("navigation", { name: "Main navigation" })).toBeVisible();
}

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

function secondPodRoster(roster: GeneratedRoster): GeneratedRoster {
  return {
    ...roster,
    roster_id: 42,
    pod_id: 4,
    assignments: roster.assignments.map((row) => ({ ...row, assignment_id: 192, employee_id: 12,
      employee_name: "Asha Nair", process_id: 8, process_name: "Digital Support", brand_id: 6, brand_name: "Cedar" })),
    employee_statuses: roster.employee_statuses.map((row) => ({ ...row,
      employee_id: row.employee_id === 10 ? 12 : 13, employee_name: row.employee_id === 10 ? "Asha Nair" : "Dev Rao" })),
    coverage: roster.coverage.map((row) => ({ ...row, process_id: 8, brand_id: 6 })),
  };
}

async function installApiMocks(page: Page, infeasibleGeneration = false, availabilityConflicts = false, multiPod = false) {
  let lockState = false;
  let availabilitySaved = false;
  let storedRoster: GeneratedRoster | null = null;
  const podLeads = new Map<number, number | null>([[3, null], [4, null]]);
  let leaveRows: { id: number; employee_id: number; starts_at: string; ends_at: string; leave_type: string; status: "requested" | "approved" | "rejected" | "cancelled"; reason: string | null }[] = [];
  await page.route(`${apiOrigin}/**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const path = url.pathname;
    const method = request.method();
    const json = (data: unknown, status = 200) => route.fulfill({ status, contentType: "application/json", body: JSON.stringify(data) });
    if (path === "/health") return json({ status: "ok" });
    if (request.headers().authorization !== basicHeader) return json({ detail: "Invalid username or password." }, 401);
    if (path === "/organizations" && method === "GET") return json([{ id: 1, name: "Northstar Operations" }]);
    if (path === "/organizations/1/pods" && method === "GET") return json([
      { id: 3, organization_id: 1, name: "Customer Operations", lead_employee_id: podLeads.get(3) },
      ...(multiPod ? [{ id: 4, organization_id: 1, name: "Digital Operations", lead_employee_id: podLeads.get(4) }] : []),
    ]);
    const podLeadRoute = path.match(/^\/organizations\/1\/pods\/(\d+)\/lead$/);
    if (podLeadRoute && method === "PUT") {
      const podId = Number(podLeadRoute[1]);
      podLeads.set(podId, (request.postDataJSON() as { employee_id: number | null }).employee_id);
      return json({ id: podId, organization_id: 1, name: podId === 3 ? "Customer Operations" : "Digital Operations", lead_employee_id: podLeads.get(podId) });
    }
    if (path === "/organizations/1/brands") return json([
      { id: 5, organization_id: 1, pod_id: 3, name: "Northstar" },
      ...(multiPod ? [{ id: 6, organization_id: 1, pod_id: 4, name: "Cedar" }] : []),
    ]);
    if (path === "/organizations/1/processes") return json([
      { id: 7, organization_id: 1, pod_id: 3, brand_id: 5, name: "Customer Care", timezone: "UTC" },
      ...(multiPod ? [{ id: 8, organization_id: 1, pod_id: 4, brand_id: 6, name: "Digital Support", timezone: "UTC" }] : []),
    ]);
    if (path === "/organizations/1/employees") return json([
      { id: 10, organization_id: 1, pod_id: 3, full_name: "Maya Chen", email: "maya@example.test", role: "Associate", is_active: true },
      { id: 11, organization_id: 1, pod_id: 3, full_name: "Leo Park", email: "leo@example.test", role: "Associate", is_active: true },
      ...(multiPod ? [
        { id: 12, organization_id: 1, pod_id: 4, full_name: "Asha Nair", email: "asha@example.test", role: "Associate", is_active: true },
        { id: 13, organization_id: 1, pod_id: 4, full_name: "Dev Rao", email: "dev@example.test", role: "Associate", is_active: true },
      ] : []),
    ]);
    if (path === "/organizations/1/rosters/generate" && method === "POST") {
      const body = request.postDataJSON() as { pod_id: number; year: number; month: number; process_id?: number; preserve_locks_from_roster_id?: number };
      if (lockState && body.preserve_locks_from_roster_id !== 41) {
        return json({ detail: "Locked roster must be supplied during regeneration." }, 409);
      }
      const roster = fixtureRoster(body.year, body.month);
      const podRoster = multiPod && body.pod_id === 4 ? secondPodRoster(roster) : { ...roster, pod_id: body.pod_id };
      if (infeasibleGeneration) {
        podRoster.success = false;
        podRoster.roster_id = null;
        podRoster.status = "infeasible";
        podRoster.error = "Hard staffing requirements cannot be met.";
        podRoster.assignments = [];
        podRoster.coverage[0] = { ...podRoster.coverage[0], scheduled: 0, uncovered: 1 };
        return json(podRoster, 409);
      }
      podRoster.assignments[0].is_locked = lockState;
      storedRoster = podRoster;
      return json(podRoster, 201);
    }
    if (path === "/organizations/1/rosters/lookup" && method === "GET") {
      if (multiPod) {
        const requestedPod = Number(url.searchParams.get("pod_id"));
        const roster = fixtureRoster(Number(url.searchParams.get("year")), Number(url.searchParams.get("month")));
        return json(requestedPod === 4 ? secondPodRoster(roster) : roster);
      }
      if (storedRoster) return json(storedRoster);
      return json({ detail: "No roster exists for this Pod and month." }, 404);
    }
    const validationRoute = path.match(/^\/organizations\/1\/rosters\/(\d+)\/(validate|revalidate)$/);
    if (validationRoute) {
      const rosterId = Number(validationRoute[1]);
      if (availabilityConflicts && availabilitySaved && rosterId === 41 && path.endsWith("/revalidate")) {
        return json({ roster_id: rosterId, valid: false, conflict_count: 2, conflicts: [
          { conflict_type: "weekly_off", work_date: "2026-12-07", employee_ids: [10], brand_id: 5,
            process_id: 7, severity: "error", message: "Employee availability does not allow this assignment.",
            assignment_ids: [91], replacement_employee_ids: [] },
          { conflict_type: "process_coverage_gap", work_date: "2026-12-07", employee_ids: [], brand_id: 5,
            process_id: 7, severity: "error", message: "Process requires 1 employee(s); 0 are assigned.",
            assignment_ids: [], replacement_employee_ids: [] },
        ] });
      }
      return json({ roster_id: rosterId, valid: true, conflict_count: 0, conflicts: [] });
    }
    if (path === "/organizations/1/rosters/41/assignments/91/candidates" && method === "GET") {
      return json({ roster_id: 41, assignment_id: 91, candidate_employee_ids: [11] });
    }
    if (path === "/organizations/1/processes/7/calendar/effective") return json([]);
    if (path === "/organizations/1/employees/10/process-assignments") return json([
      { id: 110, employee_id: 10, process_id: 7, process_name: "Customer Care", brand_id: 5, brand_name: "Northstar", effective_from: "2026-01-01", effective_to: null },
    ]);
    if (path === "/organizations/1/employees/11/process-assignments") return json([
      { id: 111, employee_id: 11, process_id: 7, process_name: "Customer Care", brand_id: 5, brand_name: "Northstar", effective_from: "2026-01-01", effective_to: null },
    ]);
    if (path === "/organizations/1/leave-requests" && method === "GET") return json(leaveRows);
    if (path === "/organizations/1/leave-requests" && method === "POST") {
      const body = request.postDataJSON() as Omit<(typeof leaveRows)[number], "id" | "status">;
      const created = { ...body, id: leaveRows.length + 1, status: "requested" as const };
      leaveRows = [...leaveRows, created];
      return json(created, 201);
    }
    const leaveStatusMatch = path.match(/^\/organizations\/1\/leave-requests\/(\d+)$/);
    if (leaveStatusMatch && method === "PATCH") {
      const id = Number(leaveStatusMatch[1]);
      const status = (request.postDataJSON() as { status: (typeof leaveRows)[number]["status"] }).status;
      leaveRows = leaveRows.map((row) => row.id === id ? { ...row, status } : row);
      const updated = leaveRows.find((row) => row.id === id);
      return updated ? json(updated) : json({ detail: "Leave request not found" }, 404);
    }
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
    if (path.endsWith("/availability") && method === "POST") {
      availabilitySaved = true;
      return json({ id: 30 }, 201);
    }
    return json({ detail: `Unmocked test API call: ${method} ${path}` }, 404);
  });
}

test("login rejects invalid credentials and clears in-memory access on logout and reload", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await page.getByLabel("Username").fill("wrong-user");
  await page.getByLabel("Password").fill("wrong-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".auth-error")).toContainText("username or password is incorrect");
  await expect(page.getByRole("heading", { name: "Good morning, team." })).toHaveCount(0);

  await signIn(page);
  expect(await page.evaluate(() => [localStorage.length, sessionStorage.length])).toEqual([0, 0]);
  await page.getByRole("button", { name: "Log out" }).click();
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
});

test("dashboard to generated roster, conflict review, manual edit, and assignment lock", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await signIn(page);
  await expect(page.getByText("2", { exact: true }).first()).toBeVisible();

  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await page.getByRole("button", { name: "Generate roster" }).click();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Customer Care.*Northstar/ }).first()).toBeVisible();

  await page.getByRole("button", { name: "Conflicts" }).click();
  await expect(page.getByText("Roster passed independent validation")).toBeVisible();

  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await page.getByRole("button", { name: /Customer Care.*Northstar/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByLabel("Employee").locator("option")).toHaveCount(2);
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
  await expect(page.getByRole("heading", { name: "Sign in to your workspace" })).toBeVisible();
  await signIn(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Customer Care.*Northstar/ }).first()).toBeVisible();
});

test("organization panel saves a Pod lead through the API", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await signIn(page);
  const leadPicker = page.getByLabel("Customer Operations lead");
  await expect(leadPicker).toBeVisible();
  await leadPicker.selectOption("10");
  await expect(page.locator(".toast")).toContainText("Pod lead saved.");
  await expect(leadPicker).toHaveValue("10");
});

test("All Pods filter shows a company-wide schedule across saved Pod rosters", async ({ page }) => {
  await installApiMocks(page, false, false, true);
  await page.goto("/");
  await signIn(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await expect(page.getByLabel("Filter by pod")).toBeEnabled();
  await page.getByLabel("Filter by pod").selectOption("");

  const companyRoster = page.locator(".company-roster-panel");
  await expect(companyRoster.getByRole("heading", { name: "Company-wide roster" })).toBeVisible();
  await expect(companyRoster).toContainText("2 Pod rosters");
  await expect(companyRoster).toContainText("Customer Operations");
  await expect(companyRoster).toContainText("Digital Operations");
  await expect(companyRoster).toContainText("Maya Chen");
  await expect(companyRoster).toContainText("Asha Nair");
  await expect(companyRoster.getByRole("table").getByRole("row")).toHaveCount(3);
  await page.getByLabel("Filter by brand").selectOption("6");
  await expect(companyRoster.getByRole("table").getByRole("row")).toHaveCount(2);
  await expect(companyRoster).toContainText("Cedar");
  await page.getByLabel("Filter by process").selectOption("8");
  await expect(companyRoster.getByRole("table").getByRole("row")).toHaveCount(2);
  await page.getByLabel("Filter by employee").selectOption("12");
  await expect(companyRoster.getByRole("table").getByRole("row")).toHaveCount(2);
  await expect(companyRoster).toContainText("Asha Nair");
});

test("company-wide generation runs and independently validates each Pod", async ({ page }) => {
  await installApiMocks(page, false, false, true);
  const generatedPods: number[] = [];
  page.on("request", (request) => {
    if (request.url() === `${apiOrigin}/organizations/1/rosters/generate` && request.method() === "POST") {
      generatedPods.push((request.postDataJSON() as { pod_id: number }).pod_id);
    }
  });
  await page.goto("/");
  await signIn(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await expect(page.getByLabel("Filter by pod")).toBeEnabled();
  await page.getByLabel("Filter by pod").selectOption("");
  await expect(page.locator(".company-roster-panel")).toBeVisible();
  await page.getByRole("button", { name: "Generate all Pod rosters" }).click();

  await expect(page.locator(".toast")).toContainText("All 2 Pod rosters generated and validated.");
  await expect(page.locator(".company-generation-results article")).toHaveCount(2);
  await expect(page.locator(".company-generation-results")).toContainText("Roster v1 generated and independently validated.");
  expect(generatedPods).toEqual([3, 4]);
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
  await signIn(page);
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
  await page.getByLabel("Username").fill(demoCredentials.username);
  await page.getByLabel("Password").fill(demoCredentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByLabel("Loading workspace")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Good morning, team." })).toBeVisible();
});

test("backend connection failures show a useful error instead of sample data", async ({ page }) => {
  await page.route(`${apiOrigin}/**`, (route) => route.abort());
  await page.goto("/");
  await page.getByLabel("Username").fill(demoCredentials.username);
  await page.getByLabel("Password").fill(demoCredentials.password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.locator(".auth-error")).toContainText("Sign-in is unavailable");
  await expect(page.getByRole("heading", { name: "A clearer way to plan your people." })).toHaveCount(0);
});

test("infeasible generation surfaces uncovered staffing without showing a successful roster", async ({ page }) => {
  await installApiMocks(page, true);
  await page.goto("/");
  await signIn(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await page.getByRole("button", { name: "Generate roster" }).click();
  await expect(page.getByRole("heading", { name: "Conflict management" })).toBeVisible();
  await expect(page.getByText("Generation did not produce a roster")).toBeVisible();
  await expect(page.getByText("1 of 1 required position(s) uncovered.")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toHaveCount(0);
});

test("availability changes save through the API and revalidate a loaded roster", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await signIn(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await page.getByRole("button", { name: "Generate roster" }).click();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await page.getByRole("button", { name: "Availability" }).click();
  await page.getByLabel("Employee for weekly off").selectOption("10");
  await page.getByLabel("Weekly off day").selectOption("2");
  await page.getByRole("button", { name: "Save change and revalidate" }).click();
  await expect(page.getByText("Availability saved · roster remains valid")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Conflict management" })).toBeVisible();
});

test("December Monday weekly off reports its availability and coverage conflicts", async ({ page }) => {
  await installApiMocks(page, false, true);
  await page.clock.install({ time: new Date("2026-11-20T12:00:00") });
  await page.goto("/");
  await signIn(page);
  await page.getByRole("navigation", { name: "Main navigation" }).getByRole("button", { name: /Monthly roster/ }).click();
  await page.getByRole("button", { name: "Next month" }).click();
  await expect(page.locator(".month-control")).toContainText("December 2026");
  await page.getByRole("button", { name: "Generate roster" }).click();
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await page.getByRole("button", { name: "Availability" }).click();
  await page.getByLabel("Employee for weekly off").selectOption("10");
  await page.getByLabel("Weekly off day").selectOption("0");
  await page.getByLabel("Effective from").fill("2026-12-01");
  await page.getByLabel("Effective to").fill("2026-12-31");
  const availabilityRequest = page.waitForRequest((request) =>
    request.url() === `${apiOrigin}/organizations/1/employees/10/availability` && request.method() === "POST");
  await page.getByRole("button", { name: "Save change and revalidate" }).click();
  const request = await availabilityRequest;
  expect(request.postDataJSON()).toMatchObject({
    day_of_week: 0, is_available: false, effective_from: "2026-12-01", effective_to: "2026-12-31",
  });
  await expect(page.getByRole("heading", { name: "weekly off" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "process coverage gap" })).toBeVisible();

  await page.getByRole("button", { name: "Revalidate roster" }).click();
  await expect(page.getByRole("heading", { name: "weekly off" })).toBeVisible();
  await expect(page.getByText("No hard conflicts found")).toHaveCount(0);
});

test("approved comp-off requests can be submitted and approved through the workspace", async ({ page }) => {
  await installApiMocks(page);
  await page.goto("/");
  await signIn(page);
  await page.getByRole("button", { name: "Availability" }).click();
  await page.getByLabel("Employee requesting leave").selectOption("10");
  await page.getByLabel("Leave type").selectOption("comp_off");
  await page.getByLabel("Starts at").fill("2026-12-07T09:00");
  await page.getByLabel("Ends at").fill("2026-12-07T17:00");
  await page.getByRole("button", { name: "Submit leave request" }).click();
  await expect(page.getByRole("status")).toContainText("Leave request submitted for review");
  await expect(page.locator(".leave-request-row")).toContainText("requested");
  await expect(page.locator(".leave-request-row")).toContainText("comp_off");

  await page.locator(".leave-request-row").getByRole("button", { name: "Approve" }).click();
  await expect(page.getByRole("status")).toContainText("Leave request approved");
  await expect(page.locator(".leave-request-row")).toContainText("approved");
});
