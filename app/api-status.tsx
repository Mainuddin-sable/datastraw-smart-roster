"use client";

import { useEffect, useState } from "react";

type ApiState = "checking" | "online" | "offline";

export function ApiStatus() {
  const [state, setState] = useState<ApiState>("checking");

  useEffect(() => {
    const controller = new AbortController();
    const baseUrl = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";
    fetch(`${baseUrl.replace(/\/$/, "")}/health`, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("Health check failed");
        return response.json() as Promise<{ status?: string }>;
      })
      .then((result) => setState(result.status === "ok" ? "online" : "offline"))
      .catch(() => {
        if (!controller.signal.aborted) setState("offline");
      });
    return () => controller.abort();
  }, []);

  const label = state === "online" ? "API online" : state === "checking" ? "Checking API" : "API offline";
  return <span className={`status status-${state}`}><i />{label}</span>;
}
