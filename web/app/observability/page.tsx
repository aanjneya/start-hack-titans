import type { Metadata } from "next";
import { Suspense } from "react";
import ObservabilityDashboard from "./observability-dashboard";

export const metadata: Metadata = {
  title: "Agent Team · Line control",
  description: "See what each procurement agent found, which records it used and what it did next.",
};

export default function ObservabilityPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-muted">Loading Agent Team…</div>}>
      <ObservabilityDashboard />
    </Suspense>
  );
}
