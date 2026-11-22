import { agentMeta } from "./observability/agent-meta";

export function workspaceLocation(path: string, view: string | null, agent: string | null) {
  if (path === "/observability") {
    const title = agentMeta(agent ?? "overview").report;
    return { section: agent === "risk" || agent === "planner" ? title : "Agent Team", title };
  }
  switch (view) {
    case "findings": return { section: "Findings", title: "Findings" };
    case "emails": return { section: "Supplier email", title: "Supplier email" };
    case "risk": return { section: "Risk ratings", title: "External risk signals" };
    case "activity": return { section: "Activity", title: "Activity" };
    default: return { section: "Overview", title: "Overview" };
  }
}

export async function workspaceMetadata(path: string, query: PageProps<"/">["searchParams"]) {
  const { view, agent } = await query;
  const first = (value: string | string[] | undefined) => (Array.isArray(value) ? value[0] : value) ?? null;
  return { title: `${workspaceLocation(path, first(view), first(agent)).title} · Line control` };
}
