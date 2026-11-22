import { Suspense } from "react";
import ObservabilityDashboard from "./observability-dashboard";
import { workspaceMetadata } from "../workspace-location";

export function generateMetadata({ searchParams }: PageProps<"/observability">) {
  return workspaceMetadata("/observability", searchParams);
}

export default function ObservabilityPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-muted">Loading Agent Team…</div>}>
      <ObservabilityDashboard />
    </Suspense>
  );
}
