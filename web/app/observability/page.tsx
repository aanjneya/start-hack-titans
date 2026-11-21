import type { Metadata } from "next";
import { Suspense } from "react";
import ObservabilityDashboard from "./observability-dashboard";

export const metadata: Metadata = {
  title: "AI Observability · Line control",
  description: "Live visual reports, error tracking, and evidence traceability for every AI agent.",
};

export default function ObservabilityPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-muted">Loading AI Observability…</div>}>
      <ObservabilityDashboard />
    </Suspense>
  );
}
