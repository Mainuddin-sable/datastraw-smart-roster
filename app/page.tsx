"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import type { FormEvent } from "react";
import {
  api, ApiError, type Assignment, type Brand, type Conflict, type Employee,
  type GeneratedRoster, type Organization, type Pod, type Process, type Validation, monthRange,
} from "@/lib/api";

type View = "overview" | "roster" | "conflicts" | "availability";
type Workspace = { pods: Pod[]; brands: Brand[]; processes: Process[]; employees: Employee[] };
const navItems: { id: View; label: string; icon: string }[] = [
  { id: "overview", label: "Overview", icon: "◫" },
  { id: "roster", label: "Monthly roster", icon: "▦" },
  { id: "conflicts", label: "Conflicts", icon: "⚑" },
  { id: "availability", label: "Availability", icon: "◷" },
];
const weekDays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
const shortDays = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

function messageOf(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

function LoadingPanel() {
  return <div className="loading-panel" aria-label="Loading workspace" aria-busy="true"><i /><i /><i /><i /></div>;
}

function EmptyState({ title, detail, action }: { title: string; detail: string; action?: React.ReactNode }) {
  return <div className="empty-state"><span className="empty-mark">✳</span><h3>{title}</h3><p>{detail}</p>{action}</div>;
}

function ErrorBanner({ children, onRetry }: { children: string; onRetry?: () => void }) {
  return <div className="error-banner" role="alert"><span className="error-icon">!</span><div><strong>We couldn’t complete that request</strong><p>{children}</p>{onRetry && <button className="text-button" onClick={onRetry}>Try again</button>}</div></div>;
}

function StatCard({ icon, label, value, note, tone = "sage" }: { icon: string; label: string; value: string | number; note: string; tone?: string }) {
  return <article className="stat-card"><span className={`stat-icon ${tone}`}>{icon}</span><div className="stat-copy"><span>{label}</span><strong>{value}</strong><small>{note}</small></div><span className="stat-spark" aria-hidden="true">⌁</span></article>;
}

function ConflictList({ conflicts, employees, brands, processes }: { conflicts: Conflict[]; employees: Employee[]; brands: Brand[]; processes: Process[] }) {
  const employeeName = (id: number) => employees.find((row) => row.id === id)?.full_name || `Employee #${id}`;
  const brandName = (id: number | null) => brands.find((row) => row.id === id)?.name || (id ? `Brand #${id}` : "Brand-wide");
  const processName = (id: number | null) => processes.find((row) => row.id === id)?.name || (id ? `Process #${id}` : "All processes");
  if (!conflicts.length) return <EmptyState title="No hard conflicts found" detail="The saved roster currently meets its checked staffing, calendar, eligibility, leave, and availability rules." />;
  return <div className="conflict-list">{conflicts.map((conflict, index) => <article className="conflict-card" key={`${conflict.conflict_type}-${conflict.work_date}-${index}`}>
    <span className="conflict-severity">{conflict.severity}</span><div className="conflict-main"><div className="conflict-head"><h3>{conflict.conflict_type.replaceAll("_", " ")}</h3><time>{new Date(`${conflict.work_date}T12:00:00`).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}</time></div>
      <p>{conflict.message}</p><div className="conflict-meta"><span>{brandName(conflict.brand_id)}</span><span>{processName(conflict.process_id)}</span></div>
      {!!conflict.employee_ids.length && <div className="chip-row"><span className="meta-label">Affected</span>{conflict.employee_ids.map((id) => <span className="person-chip" key={id}>{employeeName(id)}</span>)}</div>}
      {conflict.replacement_employee_ids.length > 0 && <div className="suggestion-row"><span className="suggestion-check">✓</span><span><b>Eligible replacements</b>{conflict.replacement_employee_ids.map(employeeName).join(", ")}</span></div>}
      {conflict.conflict_type.includes("coverage_gap") && conflict.replacement_employee_ids.length === 0 && <div className="unresolved-note">No eligible and available replacement was found for this interval.</div>}
    </div>
  </article>)}</div>;
}

export default function Home() {
  const [view, setView] = useState<View>("overview");
  const [apiState, setApiState] = useState<"checking" | "online" | "offline">("checking");
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [organizationId, setOrganizationId] = useState("");
  const [workspace, setWorkspace] = useState<Workspace>({ pods: [], brands: [], processes: [], employees: [] });
  const [loading, setLoading] = useState(true);
  const [rosterLoading, setRosterLoading] = useState(false);
  const [workspaceError, setWorkspaceError] = useState("");
  const [createOrgName, setCreateOrgName] = useState("");
  const [creatingOrg, setCreatingOrg] = useState(false);
  const [createdRoster, setCreatedRoster] = useState<GeneratedRoster | null>(null);
  const [validation, setValidation] = useState<Validation | null>(null);
  const [rosterError, setRosterError] = useState("");
  const [busy, setBusy] = useState(false);
  const [month, setMonth] = useState(() => { const now = new Date(); return new Date(now.getFullYear(), now.getMonth(), 1); });
  const [selectedPod, setSelectedPod] = useState("");
  const [selectedBrand, setSelectedBrand] = useState("");
  const [selectedProcess, setSelectedProcess] = useState("");
  const [selectedEmployee, setSelectedEmployee] = useState("");
  const [holidays, setHolidays] = useState<Set<string>>(new Set());
  const [editAssignment, setEditAssignment] = useState<Assignment | null>(null);
  const [editEmployeeId, setEditEmployeeId] = useState("");
  const [eligibleAssignments, setEligibleAssignments] = useState<{ id: number; process_id: number; process_name: string; brand_name: string; effective_from: string }[]>([]);
  const [editProcessAssignmentId, setEditProcessAssignmentId] = useState("");
  const [confirmEdit, setConfirmEdit] = useState(false);
  const [toast, setToast] = useState("");
  const [availabilityEmployee, setAvailabilityEmployee] = useState("");
  const [availabilityDay, setAvailabilityDay] = useState("0");
  const [availabilityFrom, setAvailabilityFrom] = useState(() => new Date().toISOString().slice(0, 10));
  const [availabilityTo, setAvailabilityTo] = useState("");
  const orgId = Number(organizationId) || 0;

  const loadWorkspace = useCallback(async (orgId?: string) => {
    setLoading(true); setWorkspaceError("");
    try {
      const orgRows = await api.organizations();
      setOrganizations(orgRows);
      const requested = orgId || organizationId;
      const chosen = orgRows.some((row) => String(row.id) === requested) ? requested : String(orgRows[0]?.id || "");
      setOrganizationId(chosen);
      if (!chosen) { setWorkspace({ pods: [], brands: [], processes: [], employees: [] }); return; }
      const [pods, brands, processes, employees] = await Promise.all([
        api.pods(Number(chosen)), api.brands(Number(chosen)), api.processes(Number(chosen)), api.employees(Number(chosen)),
      ]);
      setWorkspace({ pods, brands, processes, employees });
      setSelectedPod((current) => pods.some((row) => String(row.id) === current) ? current : String(pods[0]?.id || ""));
      setAvailabilityEmployee((current) => employees.some((row) => String(row.id) === current) ? current : String(employees.find((row) => row.is_active)?.id || ""));
      setApiState("online");
    } catch (error) {
      setWorkspaceError(messageOf(error));
      setApiState("offline");
    } finally { setLoading(false); }
  }, [organizationId]);

  useEffect(() => {
    const timer = window.setTimeout(() => { void loadWorkspace(); }, 0);
    return () => window.clearTimeout(timer);
  }, [loadWorkspace]);
  useEffect(() => {
    let alive = true;
    api.health().then((result) => { if (alive) setApiState(result.status === "ok" ? "online" : "offline"); })
      .catch(() => { if (alive) setApiState("offline"); });
    return () => { alive = false; };
  }, []);
  useEffect(() => {
    if (loading || !orgId || !selectedPod) return;
    let alive = true;
    const timer = window.setTimeout(async () => {
      setRosterLoading(true);
      try {
        const result = await api.latestRoster(orgId, Number(selectedPod), month.getFullYear(), month.getMonth() + 1);
        if (!alive) return;
        setCreatedRoster(result);
        if (result.roster_id) {
          const [report, calendars] = await Promise.all([
            api.validate(orgId, result.roster_id),
            Promise.all(workspace.processes.map((process) => {
              const range = monthRange(month.getFullYear(), month.getMonth() + 1);
              return api.effectiveCalendar(orgId, process.id, range.start, range.end);
            })),
          ]);
          if (!alive) return;
          setValidation(report);
          setHolidays(new Set(calendars.flat().filter((day) => day.is_holiday).map((day) => day.operating_date)));
        }
      } catch (error) {
        if (!alive) return;
        setCreatedRoster(null);
        setValidation(null);
        setHolidays(new Set());
        if (!(error instanceof ApiError) || error.status !== 404) setRosterError(messageOf(error));
      } finally { if (alive) setRosterLoading(false); }
    }, 0);
    return () => { alive = false; window.clearTimeout(timer); };
  }, [loading, orgId, selectedPod, month, workspace.processes]);
  useEffect(() => { if (toast) { const id = window.setTimeout(() => setToast(""), 3600); return () => window.clearTimeout(id); } }, [toast]);
  useEffect(() => {
    if (!editEmployeeId || !organizationId || !editAssignment) return;
    let alive = true;
    api.eligibleProcesses(Number(organizationId), Number(editEmployeeId)).then((rows) => {
      if (!alive) return;
      const matching = rows.filter((row) => row.process_id === editAssignment.process_id && row.effective_from <= editAssignment.work_date && (!row.effective_to || row.effective_to >= editAssignment.work_date));
      setEligibleAssignments(matching);
      setEditProcessAssignmentId(String(matching[0]?.id || ""));
    }).catch((error: unknown) => { if (alive) { setEligibleAssignments([]); setRosterError(messageOf(error)); } });
    return () => { alive = false; };
  }, [editEmployeeId, editAssignment, organizationId]);

  const activeEmployees = useMemo(() => workspace.employees.filter((row) => row.is_active), [workspace.employees]);
  const currentMonthRoster = createdRoster?.year === month.getFullYear() && createdRoster.month === month.getMonth() + 1 && createdRoster.pod_id === Number(selectedPod) ? createdRoster : null;
  const filteredEmployees = useMemo(() => {
    const inRoster = currentMonthRoster ? new Set(currentMonthRoster.employee_statuses.map((row) => row.employee_id)) : new Set<number>();
    return workspace.employees.filter((employee) =>
      (!currentMonthRoster || inRoster.has(employee.id)) &&
      (!selectedPod || employee.pod_id === Number(selectedPod)) &&
      (!selectedEmployee || employee.id === Number(selectedEmployee)) &&
      (!selectedBrand || currentMonthRoster?.assignments.some((a) => a.employee_id === employee.id && a.brand_id === Number(selectedBrand))) &&
      (!selectedProcess || currentMonthRoster?.assignments.some((a) => a.employee_id === employee.id && a.process_id === Number(selectedProcess))));
  }, [workspace.employees, currentMonthRoster, selectedPod, selectedEmployee, selectedBrand, selectedProcess]);
  const monthYear = month.getFullYear();
  const monthNumber = month.getMonth() + 1;
  const monthDays = new Date(monthYear, monthNumber, 0).getDate();
  const filteredAssignments = useMemo(() => (currentMonthRoster?.assignments || []).filter((item) =>
    (!selectedProcess || item.process_id === Number(selectedProcess)) &&
    (!selectedBrand || item.brand_id === Number(selectedBrand)) &&
    (!selectedEmployee || item.employee_id === Number(selectedEmployee)) &&
    (!selectedPod || workspace.employees.find((employee) => employee.id === item.employee_id)?.pod_id === Number(selectedPod))), [currentMonthRoster, selectedProcess, selectedBrand, selectedEmployee, selectedPod, workspace.employees]);
  const dateKey = (day: number) => `${monthYear}-${String(monthNumber).padStart(2, "0")}-${String(day).padStart(2, "0")}`;
  const rosterCoverage = currentMonthRoster?.coverage || [];
  const uncovered = rosterCoverage.reduce((sum, row) => sum + (row.uncovered || 0), 0);
  const generationCoverage = createdRoster?.coverage || [];
  const generationGaps = generationCoverage.filter((row) => (row.uncovered || 0) > 0);

  async function createOrganization(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!createOrgName.trim()) return;
    setCreatingOrg(true); setWorkspaceError("");
    try {
      const created = await api.createOrganization(createOrgName.trim());
      setCreateOrgName(""); setToast("Workspace created"); await loadWorkspace(String(created.id));
    } catch (error) { setWorkspaceError(messageOf(error)); }
    finally { setCreatingOrg(false); }
  }

  async function generateRoster() {
    if (!orgId || !selectedPod) return;
    setBusy(true); setRosterError(""); setValidation(null); setHolidays(new Set());
    const lockedSource = currentMonthRoster?.assignments.some((assignment) => assignment.is_locked) ? currentMonthRoster : null;
    const params = {
      pod_id: Number(selectedPod), year: monthYear, month: monthNumber,
      ...(!lockedSource && selectedProcess ? { process_id: Number(selectedProcess) } : {}),
      ...(lockedSource ? { preserve_locks_from_roster_id: lockedSource.roster_id! } : {}),
    };
    try {
      const result = await api.generate(orgId, params);
      setCreatedRoster(result);
      if (result.roster_id) {
        const [report, calendars] = await Promise.all([
          api.validate(orgId, result.roster_id),
          Promise.all(workspace.processes.filter((p) => !selectedProcess || p.id === Number(selectedProcess)).map((p) => {
            const range = monthRange(monthYear, monthNumber);
            return api.effectiveCalendar(orgId, p.id, range.start, range.end);
          })),
        ]);
        setValidation(report);
        const holidayDates = calendars.flat().filter((day) => day.is_holiday).map((day) => day.operating_date);
        setHolidays(new Set(holidayDates));
      }
      setView("roster");
      setToast(result.success ? "Monthly roster generated" : "Generation completed with staffing gaps");
    } catch (error) {
      if (error instanceof ApiError && error.status === 409 && error.payload && typeof error.payload === "object" && "generation_run_id" in error.payload) {
        const result = error.payload as GeneratedRoster;
        setCreatedRoster(result); setView("conflicts"); setRosterError(result.error || error.message);
      } else if (error instanceof ApiError && error.status === 409 && error.payload && typeof error.payload === "object" && "unresolved_conflicts" in error.payload) {
        const payload = error.payload as { detail: string; unresolved_conflicts: Conflict[] };
        setValidation({ roster_id: currentMonthRoster?.roster_id || 0, valid: false, conflict_count: payload.unresolved_conflicts.length, conflicts: payload.unresolved_conflicts });
        setView("conflicts"); setRosterError(payload.detail);
      } else setRosterError(messageOf(error));
    } finally { setBusy(false); }
  }

  async function refreshValidation() {
    if (!orgId || !currentMonthRoster?.roster_id) return;
    setBusy(true); setRosterError("");
    try { const report = await api.revalidate(orgId, currentMonthRoster.roster_id); setValidation(report); setToast(report.valid ? "Roster revalidated · no hard conflicts" : `${report.conflict_count} roster conflicts found`); }
    catch (error) { setRosterError(messageOf(error)); }
    finally { setBusy(false); }
  }

  async function saveEdit() {
    if (!orgId || !currentMonthRoster?.roster_id || !editAssignment || !editEmployeeId || !editProcessAssignmentId) return;
    setBusy(true); setRosterError("");
    try {
      const before = await api.validate(orgId, currentMonthRoster.roster_id);
      setValidation(before);
      await api.editAssignment(orgId, currentMonthRoster.roster_id, editAssignment.assignment_id, Number(editEmployeeId), Number(editProcessAssignmentId));
      const nextEmployee = workspace.employees.find((item) => item.id === Number(editEmployeeId));
      const nextEligibility = eligibleAssignments.find((item) => item.id === Number(editProcessAssignmentId));
      setCreatedRoster((current) => current ? { ...current, assignments: current.assignments.map((item) => item.assignment_id === editAssignment.assignment_id ? {
        ...item, employee_id: Number(editEmployeeId), employee_name: nextEmployee?.full_name || item.employee_name,
        process_assignment_id: Number(editProcessAssignmentId), process_name: nextEligibility?.process_name || item.process_name,
        brand_name: nextEligibility?.brand_name || item.brand_name,
      } : item) } : current);
      const after = await api.validate(orgId, currentMonthRoster.roster_id); setValidation(after);
      setEditAssignment(null); setConfirmEdit(false); setToast("Assignment updated and validated");
    } catch (error) { setRosterError(messageOf(error)); }
    finally { setBusy(false); }
  }

  async function toggleLock(assignment: Assignment) {
    if (!orgId || !currentMonthRoster?.roster_id) return;
    setBusy(true); setRosterError("");
    try {
      const result = await api.lockAssignment(orgId, currentMonthRoster.roster_id, assignment.assignment_id, !assignment.is_locked);
      setCreatedRoster((current) => current ? { ...current, assignments: current.assignments.map((item) => item.assignment_id === assignment.assignment_id ? { ...item, is_locked: result.is_locked } : item) } : current);
      setEditAssignment((current) => current?.assignment_id === assignment.assignment_id ? { ...current, is_locked: result.is_locked } : current);
      setToast(result.is_locked ? "Assignment locked for regeneration" : "Assignment unlocked");
    } catch (error) { setRosterError(messageOf(error)); }
    finally { setBusy(false); }
  }

  async function saveAvailability(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!orgId || !availabilityEmployee) return;
    setBusy(true); setRosterError("");
    try {
      await api.createAvailability(orgId, Number(availabilityEmployee), {
        day_of_week: Number(availabilityDay), sequence: 1, is_available: false,
        effective_from: availabilityFrom, ...(availabilityTo ? { effective_to: availabilityTo } : {}),
      });
      setToast("Availability change saved");
      if (currentMonthRoster?.roster_id) {
        const report = await api.revalidate(orgId, currentMonthRoster.roster_id); setValidation(report);
        setView("conflicts"); setToast(report.valid ? "Availability saved · roster remains valid" : `Availability saved · ${report.conflict_count} conflicts need attention`);
      }
    } catch (error) { setRosterError(messageOf(error)); }
    finally { setBusy(false); }
  }

  function shiftMonth(amount: number) { setMonth((current) => new Date(current.getFullYear(), current.getMonth() + amount, 1)); }
  const monthLabel = month.toLocaleDateString(undefined, { month: "long", year: "numeric" });
  const hasOrganization = organizations.length > 0;
  const rosterEmptyAction = workspace.pods.length
    ? workspace.processes.length
      ? <button className="button-primary" onClick={() => void generateRoster()} disabled={busy || rosterLoading}>{busy ? "Generating..." : "Generate this month"}</button>
      : <a className="inline-link" href="http://localhost:8000/docs" target="_blank" rel="noreferrer">Configure a Process, calendar, and staffing rule ↗</a>
    : <a className="inline-link" href="http://localhost:8000/docs" target="_blank" rel="noreferrer">Open organization API docs</a>;

  return <main className="app-shell">
    <aside className="sidebar">
      <a className="logo-lockup" href="#dashboard" onClick={(event) => { event.preventDefault(); setView("overview"); }}><span className="logo-icon">d</span><span>datastraw<small>SMART ROSTER</small></span></a>
      <div className="workspace-select-wrap"><label htmlFor="workspace-select">WORKSPACE</label><select id="workspace-select" value={organizationId} onChange={(event) => { setCreatedRoster(null); setValidation(null); void loadWorkspace(event.target.value); }} disabled={!organizations.length}>
        {organizations.length ? organizations.map((row) => <option key={row.id} value={row.id}>{row.name}</option>) : <option value="">No workspace</option>}
      </select></div>
      <nav className="primary-nav" aria-label="Main navigation"><p className="nav-caption">WORKSPACE</p>{navItems.map((item) => <button key={item.id} className={`nav-link ${view === item.id ? "active" : ""}`} onClick={() => setView(item.id)}><span className="nav-icon">{item.icon}</span>{item.label}{item.id === "conflicts" && validation && validation.conflict_count > 0 && <span className="nav-count">{validation.conflict_count}</span>}</button>)}</nav>
      <div className="sidebar-bottom"><div className="help-card"><span className="help-symbol">✳</span><div><strong>Plan with confidence</strong><p>Review coverage before you publish.</p></div></div><span className="build-label">DEVELOPMENT WORKSPACE</span></div>
    </aside>
    <section className="main-area">
      <header className="topbar"><div className="mobile-brand"><span className="logo-icon">d</span>datastraw</div><div className="breadcrumbs"><span>Workspace</span><b>/</b><strong>{navItems.find((item) => item.id === view)?.label}</strong></div><div className="top-actions"><span className={`api-pill ${apiState}`}><i />{apiState === "online" ? "API connected" : apiState === "checking" ? "Connecting" : "API unavailable"}</span><button className="avatar" aria-label="Development user">DS</button></div></header>
      <div className="page-content">
        {workspaceError && <ErrorBanner onRetry={() => void loadWorkspace()}>{workspaceError}</ErrorBanner>}
        {!hasOrganization && !loading && !workspaceError && <section className="welcome-panel"><span className="welcome-kicker">WELCOME TO DATASTRAW</span><h1>A clearer way to plan your people.</h1><p>Create a workspace to connect your organization structure and begin building real rosters.</p><form className="create-workspace" onSubmit={(event) => void createOrganization(event)}><label htmlFor="org-name">Organization name</label><div><input id="org-name" value={createOrgName} onChange={(event) => setCreateOrgName(event.target.value)} placeholder="e.g. Northstar Operations" required /><button className="button-primary" disabled={creatingOrg}>{creatingOrg ? "Creating…" : "Create workspace"}</button></div></form><small>This creates an organization using the connected FastAPI service. No sample data is added.</small></section>}
        {loading && <LoadingPanel />}
        {!loading && hasOrganization && <>
          {view === "overview" && <>
            <div className="page-heading"><div><div className="eyebrow-row"><span className="eyebrow-dot" /> OPERATIONS OVERVIEW <span className="eyebrow-date">{new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" })}</span></div><h1>Good morning, team.</h1><p>Your workforce at a glance. Keep coverage, people, and plans moving together.</p></div><button className="button-primary" onClick={() => setView("roster")}><span>＋</span> Plan a roster</button></div>
            <div className="stats-grid"><StatCard icon="♙" label="Active employees" value={activeEmployees.length} note={`${workspace.employees.length} people in workspace`} /><StatCard icon="◈" label="Brands" value={workspace.brands.length} note="Across your organization" tone="lavender" /><StatCard icon="⌘" label="Processes" value={workspace.processes.length} note={`${workspace.pods.length} operational pods`} tone="blue" /><StatCard icon="▦" label="Staffing coverage" value={createdRoster?.success ? `${Math.max(0, 100 - Math.round((uncovered / Math.max(1, rosterCoverage.reduce((sum, row) => sum + row.required, 0))) * 100))}%` : "—"} note={createdRoster?.success ? (validation?.valid ? "Validated against current rules" : `${validation?.conflict_count ?? generationGaps.length} open issue(s)`) : "Generate a roster to see coverage"} tone="amber" /></div>
            <div className="dashboard-grid"><section className="panel roster-summary"><div className="panel-title"><div><span className="section-kicker">ROSTER PLANNING</span><h2>{createdRoster?.roster_id ? monthLabel : "Monthly roster"}</h2></div>{createdRoster?.roster_id && <span className={`status-badge ${validation?.valid ? "good" : "attention"}`}>{validation?.valid ? "Validated" : "Review needed"}</span>}</div>
              {createdRoster?.roster_id ? <><div className="summary-metrics"><div><strong>{createdRoster.assignments.length}</strong><span>work assignments</span></div><div><strong>{createdRoster.employee_statuses.filter((row) => row.status === "working").length}</strong><span>employee-days</span></div><div><strong>{validation?.conflict_count ?? generationGaps.length}</strong><span>open conflicts</span></div></div><div className="coverage-line"><span>Coverage requirements</span><b>{rosterCoverage.reduce((sum, row) => sum + row.required, 0) - uncovered}<small> / {rosterCoverage.reduce((sum, row) => sum + row.required, 0)} staffed</small></b></div><div className="progress-track"><i style={{ width: `${Math.max(0, 100 - (uncovered / Math.max(1, rosterCoverage.reduce((sum, row) => sum + row.required, 0))) * 100)}%` }} /></div><button className="inline-link" onClick={() => setView("roster")}>Open monthly roster <span>→</span></button></> : <EmptyState title="No roster generated yet" detail="Generate a schedule to see assignments, staffing coverage, and validation results." action={<button className="button-primary compact" onClick={() => setView("roster")}>Start monthly planning</button>} />}</section>
              <section className="panel structure-panel"><div className="panel-title"><div><span className="section-kicker">YOUR ORGANIZATION</span><h2>Operational structure</h2></div><span className="structure-glyph">⌘</span></div><div className="structure-list"><div><span className="structure-dot pod-dot" /><span>Pods</span><b>{workspace.pods.length}</b></div><div><span className="structure-dot brand-dot" /><span>Brands</span><b>{workspace.brands.length}</b></div><div><span className="structure-dot process-dot" /><span>Processes</span><b>{workspace.processes.length}</b></div><div><span className="structure-dot people-dot" /><span>Active employees</span><b>{activeEmployees.length}</b></div></div><div className="structure-foot">Live counts from your organization API</div></section></div>
            {generationGaps.length > 0 && <section className="attention-strip"><span>!</span><div><b>Staffing requirements need attention</b><p>{generationGaps.length} requirement(s) remain uncovered in the latest generation result.</p></div><button onClick={() => setView("conflicts")}>Review conflicts →</button></section>}
          </>}

          {view === "roster" && <>
            <div className="page-heading roster-heading"><div><div className="eyebrow-row"><span className="eyebrow-dot" /> SCHEDULE BUILDER</div><h1>Monthly roster</h1><p>Generate and refine a month of work across your teams.</p></div><button className="button-primary" disabled={busy || rosterLoading || !selectedPod || !workspace.processes.length} onClick={() => void generateRoster()}>{busy ? <><span className="spinner" /> Generating…</> : <><span>✧</span> Generate roster</>}</button></div>
            {rosterError && <ErrorBanner>{rosterError}</ErrorBanner>}
            <section className="panel roster-tools"><div className="month-control"><button aria-label="Previous month" onClick={() => shiftMonth(-1)}>‹</button><strong>{monthLabel}</strong><button aria-label="Next month" onClick={() => shiftMonth(1)}>›</button></div><div className="filter-row"><label>Pod<select aria-label="Filter by pod" value={selectedPod} onChange={(event) => setSelectedPod(event.target.value)}><option value="">All pods</option>{workspace.pods.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>Brand<select aria-label="Filter by brand" value={selectedBrand} onChange={(event) => setSelectedBrand(event.target.value)}><option value="">All brands</option>{workspace.brands.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>Process<select aria-label="Filter by process" value={selectedProcess} onChange={(event) => setSelectedProcess(event.target.value)}><option value="">All processes</option>{workspace.processes.filter((row) => !selectedBrand || row.brand_id === Number(selectedBrand)).map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></label><label>Employee<select aria-label="Filter by employee" value={selectedEmployee} onChange={(event) => setSelectedEmployee(event.target.value)}><option value="">All employees</option>{workspace.employees.filter((row) => !selectedPod || row.pod_id === Number(selectedPod)).map((row) => <option key={row.id} value={row.id}>{row.full_name}</option>)}</select></label></div></section>
            {currentMonthRoster?.assignments.some((assignment) => assignment.is_locked) && <div className="info-callout lock-callout"><span>◆</span><p>Manual locks are active. The next generation keeps every valid lock and runs at full Pod scope, even if a Process filter is selected.</p></div>}
            {rosterLoading ? <LoadingPanel /> : currentMonthRoster?.roster_id ? <>
              <div className="roster-meta"><span className={`status-badge ${validation?.valid ? "good" : validation ? "attention" : "neutral"}`}><i />{validation ? (validation.valid ? "Validation passed" : `${validation.conflict_count} conflicts · not cleared`) : "Not yet validated"}</span><span>Roster version {currentMonthRoster.roster_version ?? "—"}</span><button className="subtle-button" onClick={() => void refreshValidation()} disabled={busy}>↻ Revalidate</button></div>
              {validation && !validation.valid && <div className="unresolved-note roster-warning" role="alert">This roster has unresolved hard conflicts and is not marked as valid. Review conflicts before relying on this schedule.</div>}
              <section className="panel roster-grid-panel"><div className="grid-title"><div><h2>Team schedule</h2><p>{filteredEmployees.length} employees · {monthDays} days</p></div><div className="legend"><span><i className="legend-work" />Assigned</span><span><i className="legend-off" />Day off</span><span><i className="legend-leave" />Leave</span><span><i className="legend-conflict" />Conflict</span></div></div><div className="roster-scroll"><table className="roster-grid"><thead><tr><th className="employee-col">EMPLOYEE</th>{Array.from({ length: monthDays }, (_, index) => { const day = index + 1; const weekday = shortDays[(new Date(monthYear, monthNumber - 1, day).getDay() + 6) % 7]; return <th key={day} className={weekday === "Sun" || weekday === "Sat" ? "weekend-col" : ""}><span>{weekday}</span><b>{String(day).padStart(2, "0")}</b>{holidays.has(dateKey(day)) && <i className="holiday-dot" title="Public holiday" />}</th>; })}</tr></thead><tbody>{filteredEmployees.length ? filteredEmployees.map((employee) => <tr key={employee.id}><th className="employee-col"><span className="employee-avatar">{employee.full_name.split(" ").map((part) => part[0]).slice(0, 2).join("").toUpperCase()}</span><span className="employee-name"><b>{employee.full_name}</b><small>{employee.role || "Team member"}</small></span></th>{Array.from({ length: monthDays }, (_, index) => { const day = index + 1; const key = dateKey(day); const status = currentMonthRoster.employee_statuses.find((item) => item.employee_id === employee.id && item.work_date === key)?.status; const blocks = filteredAssignments.filter((item) => item.employee_id === employee.id && item.work_date === key); const flagged = validation?.conflicts.some((conflict) => conflict.work_date === key && conflict.employee_ids.includes(employee.id)); const stateClass = flagged ? "has-conflict" : holidays.has(key) && (!blocks.length) ? "is-holiday" : status === "leave" ? "is-leave" : status === "day_off" ? "is-off" : ""; return <td key={key} className={`${stateClass} ${[5, 6].includes((new Date(monthYear, monthNumber - 1, day).getDay() + 6) % 7) ? "weekend-cell" : ""}`}>{blocks.map((assignment) => <button className={`assignment-pill ${assignment.is_locked ? "locked" : ""}`} key={assignment.assignment_id} title={`${assignment.brand_name} · ${assignment.process_name} · ${new Date(assignment.starts_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}–${new Date(assignment.ends_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}${assignment.is_locked ? " · locked" : ""}`} onClick={() => { setEditAssignment(assignment); setEditEmployeeId(String(assignment.employee_id)); setConfirmEdit(false); }}><span>{assignment.process_name}</span><small>{assignment.brand_name}</small>{assignment.is_locked && <b aria-label="Locked">◆</b>}</button>)}{!blocks.length && status && <span className="day-state">{holidays.has(key) ? "Holiday" : status === "leave" ? "Leave" : status === "day_off" ? "Off" : status === "unassigned" ? "—" : ""}</span>}</td>; })}</tr>) : <tr><td colSpan={monthDays + 1}><EmptyState title="No employees match these filters" detail="Try a different pod, brand, process, or employee." /></td></tr>}</tbody></table></div><div className="grid-foot"><span><i className="holiday-dot" /> Public holiday dates resolved from process calendars</span><span>Work blocks reflect saved API assignments</span></div></section>
            </> : <section className="panel roster-empty"><EmptyState title={!workspace.pods.length ? "Add a Pod before generating" : !workspace.processes.length ? "Set up a Process first" : "No saved roster for this month"} detail={!workspace.pods.length ? "Roster generation is scoped to a Pod. Create one through the organization API, then return here." : !workspace.processes.length ? "Generation needs a Process with an operating calendar and a positive staffing requirement. Configure those through the connected API." : "No saved roster exists for this Pod and month. Generate one to see assignments, daily status, and coverage."} action={rosterEmptyAction} /></section>}
          </>}

          {view === "conflicts" && <>
            <div className="page-heading"><div><div className="eyebrow-row"><span className="eyebrow-dot coral" /> ROSTER QUALITY</div><h1>Conflict management</h1><p>Independent checks for coverage, availability, leave, eligibility, and operating hours.</p></div>{currentMonthRoster?.roster_id && <button className="button-secondary" onClick={() => void refreshValidation()} disabled={busy}>↻ Revalidate roster</button>}</div>
            {rosterError && <ErrorBanner>{rosterError}</ErrorBanner>}
            {validation ? <><div className={`validation-banner ${validation.valid ? "success" : "warning"}`}><span>{validation.valid ? "✓" : "!"}</span><div><strong>{validation.valid ? "Roster passed independent validation" : `${validation.conflict_count} issue${validation.conflict_count === 1 ? "" : "s"} need attention`}</strong><p>{validation.valid ? "No unresolved hard conflicts were returned by the API." : "The roster is not marked clear while hard conflicts remain."}</p></div><span className="validation-date">Roster #{validation.roster_id}</span></div><ConflictList conflicts={validation.conflicts} employees={workspace.employees} brands={workspace.brands} processes={workspace.processes} /></> : createdRoster && !createdRoster.success ? <><div className="validation-banner warning"><span>!</span><div><strong>Generation did not produce a roster</strong><p>{createdRoster.error || "The solver could not satisfy all hard staffing requirements."}</p></div></div><div className="conflict-list">{generationGaps.map((gap, index) => <article key={`${gap.scope}-${gap.work_date}-${index}`} className="conflict-card"><span className="conflict-severity">gap</span><div className="conflict-main"><div className="conflict-head"><h3>{gap.scope} staffing gap</h3><time>{gap.work_date}</time></div><p>{gap.uncovered == null ? "Exact uncovered count unavailable" : `${gap.uncovered} of ${gap.required} required position(s) uncovered.`}</p><div className="conflict-meta"><span>{workspace.brands.find((row) => row.id === gap.brand_id)?.name || `Brand #${gap.brand_id}`}</span><span>{workspace.processes.find((row) => row.id === gap.process_id)?.name || (gap.process_id ? `Process #${gap.process_id}` : "Brand-wide")}</span></div></div></article>)}</div></> : <EmptyState title="No validation report yet" detail="Generate a roster or revalidate a loaded roster to inspect its current hard constraints." action={<button className="button-primary" onClick={() => setView("roster")}>Open monthly roster</button>} />}
          </>}

          {view === "availability" && <>
            <div className="page-heading"><div><div className="eyebrow-row"><span className="eyebrow-dot blue" /> TEAM AVAILABILITY</div><h1>Availability changes</h1><p>Record recurring weekly unavailability and immediately recheck the loaded roster.</p></div></div>
            {rosterError && <ErrorBanner>{rosterError}</ErrorBanner>}
            <div className="availability-layout"><section className="panel availability-form-panel"><div className="panel-title"><div><span className="section-kicker">NEW AVAILABILITY RULE</span><h2>Mark a weekly off</h2></div><span className="form-symbol">◷</span></div><p className="form-intro">This records an unavailable weekday from an effective date. The change is saved through the employee availability API.</p><form className="availability-form" onSubmit={(event) => void saveAvailability(event)}><label>Employee<select required value={availabilityEmployee} onChange={(event) => setAvailabilityEmployee(event.target.value)}><option value="">Choose an employee</option>{activeEmployees.map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name} · {workspace.pods.find((pod) => pod.id === employee.pod_id)?.name || "Pod"}</option>)}</select></label><label>Weekly off day<select value={availabilityDay} onChange={(event) => setAvailabilityDay(event.target.value)}>{weekDays.map((day, index) => <option key={day} value={index}>{day}</option>)}</select></label><div className="date-fields"><label>Effective from<input type="date" required value={availabilityFrom} onChange={(event) => setAvailabilityFrom(event.target.value)} /></label><label>Effective to <span className="optional-label">optional</span><input type="date" min={availabilityFrom} value={availabilityTo} onChange={(event) => setAvailabilityTo(event.target.value)} /></label></div><div className="info-callout"><span>i</span><p>Availability rules are recurring and effective-dated. The API prevents overlapping rules. This action can reveal coverage gaps in the loaded roster.</p></div><button className="button-primary full-width" disabled={busy || !activeEmployees.length}>{busy ? <><span className="spinner" /> Saving & checking…</> : "Save change and revalidate"}</button></form></section><aside className="panel availability-note"><span className="note-orbit">◷</span><span className="section-kicker">WHAT HAPPENS NEXT</span><h2>One change.<br />A clearer plan.</h2><ol><li><b>01</b><span>Save the weekly availability rule.</span></li><li><b>02</b><span>Revalidate the current roster against the new rule.</span></li><li><b>03</b><span>Review coverage gaps and eligible replacements.</span></li></ol>{!currentMonthRoster && <p className="note-foot">No roster is loaded right now. Your availability rule will still be saved; generate a roster afterward to review its impact.</p>}</aside></div>
            <div className="unsupported-note"><span>ⓘ</span><div><b>Leave management is not exposed by the current backend API.</b><p>You can review leave conflicts returned by roster validation, but creating or changing leave requests is not available in this workspace yet.</p></div></div>
          </>}
        </>}
      </div>
      <footer className="app-footer"><span>DATASTRAW <b>·</b> SMART ROSTER</span><span>Development workspace · Authentication is not enabled</span></footer>
    </section>

    {editAssignment && <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setEditAssignment(null); }}><section className="edit-dialog" role="dialog" aria-modal="true" aria-labelledby="edit-title"><button className="dialog-close" aria-label="Close edit assignment" onClick={() => setEditAssignment(null)}>×</button><span className="section-kicker">MANUAL SCHEDULE CHANGE</span><h2 id="edit-title">Edit assignment</h2><p>Move this work block to another employee eligible for {editAssignment.process_name} on {editAssignment.work_date}.</p><div className="assignment-context"><span className="context-icon">▦</span><div><b>{editAssignment.process_name}</b><small>{editAssignment.brand_name} · {new Date(editAssignment.starts_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })} – {new Date(editAssignment.ends_at).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}</small></div>{editAssignment.is_locked && <span className="locked-label">◆ Locked</span>}</div>
      {editAssignment.is_locked ? <div className="info-callout"><span>i</span><p>Unlock this assignment before editing it.</p></div> : <><label className="dialog-label">Employee<select value={editEmployeeId} onChange={(event) => setEditEmployeeId(event.target.value)}>{activeEmployees.filter((employee) => employee.pod_id === Number(selectedPod)).map((employee) => <option key={employee.id} value={employee.id}>{employee.full_name}{employee.id === editAssignment.employee_id ? " · currently assigned" : ""}</option>)}</select></label><label className="dialog-label">Process eligibility<select value={editProcessAssignmentId} onChange={(event) => setEditProcessAssignmentId(event.target.value)} disabled={!eligibleAssignments.length}><option value="">{eligibleAssignments.length ? "Select eligibility" : "No effective eligibility found"}</option>{eligibleAssignments.map((row) => <option key={row.id} value={row.id}>{row.process_name} · {row.brand_name} · from {row.effective_from}</option>)}</select></label><div className="edit-validation-note"><span>✓</span> The backend checks eligibility, availability, leave, calendars, and coverage before saving.</div></>}
      <div className="dialog-actions"><button className="button-secondary" onClick={() => setEditAssignment(null)}>Close</button><button className="button-secondary" disabled={busy} onClick={() => void toggleLock(editAssignment)}>{editAssignment.is_locked ? "Unlock assignment" : "Lock assignment"}</button>{!editAssignment.is_locked && <button className="button-primary" disabled={busy || !eligibleAssignments.length || !editProcessAssignmentId} onClick={() => setConfirmEdit(true)}>Validate & continue</button>}</div>
      {confirmEdit && <div className="confirm-box" role="alertdialog" aria-label="Confirm assignment edit"><strong>Confirm this roster change?</strong><p>The current roster will be checked first. The API will reject and roll back the edit if it violates hard constraints.</p><div><button className="button-secondary" onClick={() => setConfirmEdit(false)}>Go back</button><button className="button-primary" disabled={busy} onClick={() => void saveEdit()}>{busy ? "Saving…" : "Save assignment"}</button></div></div>}
      {rosterError && <p className="dialog-error" role="alert">{rosterError}</p>}</section></div>}
    {toast && <div className="toast" role="status"><span>✓</span>{toast}<button aria-label="Dismiss notification" onClick={() => setToast("")}>×</button></div>}
  </main>;
}
