const API_BASE = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(/\/$/, "");

export class ApiError extends Error {
  constructor(message: string, readonly status: number, readonly payload?: unknown) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...init?.headers },
      cache: "no-store",
    });
  } catch {
    throw new ApiError(`Can't reach the roster API at ${API_BASE}. Check that the backend is running.`, 0);
  }
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    let payload: unknown;
    try {
      const body = await response.json() as { detail?: string | { msg?: string }[] };
      payload = body;
      if (typeof body.detail === "string") detail = body.detail;
      else if (Array.isArray(body.detail)) detail = body.detail.map((item) => item.msg || "Invalid request").join(" · ");
    } catch { /* keep the HTTP fallback */ }
    throw new ApiError(detail, response.status, payload);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

const json = (method: string, body?: unknown): RequestInit => ({
  method,
  ...(body === undefined ? {} : { body: JSON.stringify(body) }),
});

export interface Organization { id: number; name: string }
export interface Pod { id: number; organization_id: number; name: string }
export interface Brand { id: number; organization_id: number; pod_id: number; name: string }
export interface Process { id: number; organization_id: number; pod_id: number; brand_id: number; name: string; timezone: string }
export interface Employee { id: number; organization_id: number; pod_id: number; full_name: string; email: string; role: string | null; is_active: boolean }
export interface EligibleProcess { id: number; employee_id: number; process_id: number; process_name: string; brand_id: number; brand_name: string; effective_from: string; effective_to: string | null }
export interface Assignment {
  assignment_id: number;
  employee_id: number;
  employee_name: string;
  process_id: number;
  process_name: string;
  brand_id: number;
  brand_name: string;
  process_assignment_id: number;
  work_date: string;
  starts_at: string;
  ends_at: string;
  is_locked: boolean;
}
export interface EmployeeStatus { employee_id: number; employee_name: string; work_date: string; status: "working" | "day_off" | "leave" | "comp_off" | "unassigned" }
export interface Coverage { scope: "brand" | "process"; work_date: string; brand_id: number; process_id: number | null; sequence: number | null; required: number; scheduled: number | null; uncovered: number | null; diagnosed?: boolean }
export interface GeneratedRoster {
  success: boolean; generation_run_id: number; roster_id: number | null; roster_version: number | null; status: string; solver_status: string;
  year: number; month: number; pod_id: number; assignments: Assignment[]; employee_statuses: EmployeeStatus[];
  coverage: Coverage[]; error: string | null;
}
export interface Conflict {
  conflict_type: string; work_date: string; employee_ids: number[]; brand_id: number | null; process_id: number | null;
  severity: string; message: string; assignment_ids: number[]; replacement_employee_ids: number[];
}
export interface Validation { roster_id: number; valid: boolean; conflict_count: number; conflicts: Conflict[] }
export interface EffectiveDay { operating_date: string; is_holiday: boolean; source: string; is_open: boolean; sequence: number; opens_at: string | null; closes_at: string | null; reason: string | null }

export const api = {
  health: () => request<{ status: string }>("/health"),
  organizations: () => request<Organization[]>("/organizations"),
  createOrganization: (name: string) => request<Organization>("/organizations", json("POST", { name })),
  pods: (org: number) => request<Pod[]>(`/organizations/${org}/pods`),
  brands: (org: number) => request<Brand[]>(`/organizations/${org}/brands`),
  processes: (org: number) => request<Process[]>(`/organizations/${org}/processes`),
  employees: (org: number) => request<Employee[]>(`/organizations/${org}/employees`),
  generate: (org: number, body: { pod_id: number; year: number; month: number; process_id?: number; preserve_locks_from_roster_id?: number }) =>
    request<GeneratedRoster>(`/organizations/${org}/rosters/generate`, json("POST", body)),
  latestRoster: (org: number, pod: number, year: number, month: number) =>
    request<GeneratedRoster>(`/organizations/${org}/rosters/lookup?pod_id=${pod}&year=${year}&month=${month}`),
  validate: (org: number, roster: number) => request<Validation>(`/organizations/${org}/rosters/${roster}/validate`, json("POST")),
  revalidate: (org: number, roster: number) => request<Validation>(`/organizations/${org}/rosters/${roster}/revalidate`, json("POST")),
  eligibleProcesses: (org: number, employee: number) => request<EligibleProcess[]>(`/organizations/${org}/employees/${employee}/process-assignments`),
  editAssignment: (org: number, roster: number, assignment: number, employee: number, processAssignment: number) =>
    request<{ assignment_id: number }>(`/organizations/${org}/rosters/${roster}/assignments/${assignment}`, json("PUT", { employee_id: employee, process_assignment_id: processAssignment })),
  lockAssignment: (org: number, roster: number, assignment: number, isLocked: boolean) =>
    request<{ assignment_id: number; is_locked: boolean }>(`/organizations/${org}/rosters/${roster}/assignments/${assignment}/lock`, json("PATCH", { is_locked: isLocked })),
  createAvailability: (org: number, employee: number, body: { day_of_week: number; sequence: number; is_available: false; effective_from: string; effective_to?: string }) =>
    request<unknown>(`/organizations/${org}/employees/${employee}/availability`, json("POST", body)),
  effectiveCalendar: (org: number, process: number, start: string, end: string) =>
    request<EffectiveDay[]>(`/organizations/${org}/processes/${process}/calendar/effective?start_date=${start}&end_date=${end}`),
};

export function monthRange(year: number, month: number): { start: string; end: string } {
  const pad = (number: number) => String(number).padStart(2, "0");
  const last = new Date(year, month, 0).getDate();
  return { start: `${year}-${pad(month)}-01`, end: `${year}-${pad(month)}-${pad(last)}` };
}
