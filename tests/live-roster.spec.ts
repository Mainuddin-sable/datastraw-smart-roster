import { expect, test } from "@playwright/test";

const apiOrigin = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const isLocalApi = (() => {
  try {
    const url = new URL(apiOrigin);
    return ["localhost", "127.0.0.1", "[::1]"].includes(url.hostname)
      && !url.username && !url.password;
  } catch {
    return false;
  }
})();
const demoUsername = process.env.E2E_DEMO_USERNAME;
const demoPassword = process.env.E2E_DEMO_PASSWORD;
const authHeaders: Record<string, string> = demoUsername && demoPassword
  ? { Authorization: `Basic ${Buffer.from(`${demoUsername}:${demoPassword}`).toString("base64")}` }
  : {};

test("live API: edit dropdown reflects real Pod-level employee and eligibility data", async ({ page }) => {
  test.skip(process.env.RUN_LIVE_API_E2E !== "1" || !demoUsername || !demoPassword || !isLocalApi,
    "Set RUN_LIVE_API_E2E=1 and E2E_DEMO_USERNAME/E2E_DEMO_PASSWORD to use an authenticated loopback API.");
  await page.clock.install({ time: new Date("2026-11-20T12:00:00") });

  const organizationsResponse = await page.request.get(`${apiOrigin}/organizations`, { headers: authHeaders });
  expect(organizationsResponse.ok()).toBeTruthy();
  const organizations = await organizationsResponse.json() as { id: number; name: string }[];
  const organization = organizations.find((row) => row.id === 1);
  expect(organization?.name).toBe("DataStraw Operations");

  const podsResponse = await page.request.get(`${apiOrigin}/organizations/1/pods`, { headers: authHeaders });
  expect(podsResponse.ok()).toBeTruthy();
  const pods = await podsResponse.json() as { id: number; name: string }[];
  expect(pods.find((row) => row.id === 1)?.name).toBe("Customer Support");
  expect(pods.find((row) => row.id === 2)?.name).toBe("Customer Care");

  const podOneLookup = await page.request.get(
    `${apiOrigin}/organizations/1/rosters/lookup?pod_id=1&year=2026&month=12`, { headers: authHeaders },
  );
  const podOneRoster = await podOneLookup.json() as {
    roster_id: number; assignments: { assignment_id: number; process_id: number; work_date: string }[];
  };
  const podOneBlock = podOneRoster.assignments.find((row) => row.process_id === 1 && row.work_date === "2026-12-01");
  expect(podOneBlock).toBeTruthy();
  const podOneCandidatesResponse = await page.request.get(
    `${apiOrigin}/organizations/1/rosters/${podOneRoster.roster_id}/assignments/${podOneBlock!.assignment_id}/candidates`, { headers: authHeaders },
  );
  expect((await podOneCandidatesResponse.json()).candidate_employee_ids).toEqual([]);

  await page.goto("/");
  await page.getByLabel("Username").fill(demoUsername!);
  await page.getByLabel("Password").fill(demoPassword!);
  await page.getByRole("button", { name: "Sign in" }).click();
  const podLeadPicker = page.getByLabel("Customer Support lead");
  await expect(podLeadPicker).toBeVisible();
  await expect(podLeadPicker.locator("option").filter({ hasText: "Rahul Sharma" })).toHaveCount(1);
  await page.getByRole("button", { name: "Monthly roster" }).click();
  await page.getByRole("button", { name: "Next month" }).click();
  await expect(page.locator(".month-control")).toContainText("December 2026");
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await page.getByRole("button", { name: /CS.*Northstar/ }).first().click();

  let employeeSelect = page.getByRole("dialog").getByLabel("Employee");
  await expect(employeeSelect.locator("option")).toHaveCount(1);
  await expect(employeeSelect).toHaveValue("1");
  await expect(page.getByLabel("Process eligibility").locator("option").nth(1)).toContainText("CS");
  await page.getByRole("button", { name: "Close", exact: true }).click();

  const podTwoLookup = await page.request.get(
    `${apiOrigin}/organizations/1/rosters/lookup?pod_id=2&year=2026&month=12`, { headers: authHeaders },
  );
  const podTwoRoster = await podTwoLookup.json() as {
    roster_id: number; assignments: { assignment_id: number; employee_id: number; process_id: number; work_date: string }[];
  };
  const podTwoBlock = podTwoRoster.assignments.find((row) => row.process_id === 5 && row.work_date === "2026-12-01");
  expect(podTwoBlock).toBeTruthy();
  const podTwoCandidatesResponse = await page.request.get(
    `${apiOrigin}/organizations/1/rosters/${podTwoRoster.roster_id}/assignments/${podTwoBlock!.assignment_id}/candidates`, { headers: authHeaders },
  );
  const podTwoCandidates = (await podTwoCandidatesResponse.json()).candidate_employee_ids as number[];
  expect(podTwoCandidates.length).toBeGreaterThan(0);

  await page.getByLabel("Filter by pod").selectOption("2");
  await expect(page.getByRole("heading", { name: "Team schedule" })).toBeVisible();
  await page.locator(`[data-assignment-id="${podTwoBlock!.assignment_id}"]`).click();
  employeeSelect = page.getByRole("dialog").getByLabel("Employee");
  await expect(employeeSelect.locator("option")).toHaveCount(podTwoCandidates.length + 1);
  const visibleEmployeeIds = await employeeSelect.locator("option").evaluateAll((options) =>
    options.map((option) => Number((option as HTMLOptionElement).value)),
  );
  expect(new Set(visibleEmployeeIds)).toEqual(new Set([podTwoBlock!.employee_id, ...podTwoCandidates]));
  await expect(page.getByLabel("Process eligibility").locator("option").nth(1)).toContainText("Orders");
});
