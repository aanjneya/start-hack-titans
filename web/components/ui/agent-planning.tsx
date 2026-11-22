"use client";

// Agent planning card: the "agent is working" timeline shown in chat after a prompt.
// Adapted from a pasted AgentPlanning demo and restyled to the workspace's green-and-white step card.
// The parent drives every status change; this component keeps no timers.

import * as React from "react";
import { AlertTriangle, Check, ChevronRight, Loader2, X } from "lucide-react";

import { cn } from "@/lib/utils";

export type PlanStepStatus = "pending" | "active" | "success" | "error";

export interface PlanStep {
  id: string;
  title: string;
  status: PlanStepStatus;
  /** One muted line under the title (e.g. files read). */
  detail?: React.ReactNode;
  /** Small chips after the title (e.g. "truth store", "Planner"). */
  tags?: string[];
  /** Optional expandable rich content (click the step to toggle). */
  content?: React.ReactNode;
  icon?: React.ReactNode;
  duration?: string;
  defaultExpanded?: boolean;
}

export interface AgentPlanningProps {
  /** Header text. */
  title?: string;
  steps?: PlanStep[];
  /** Total time shown at the right of the header, e.g. "2.4s". */
  elapsed?: string;
  /** Overall state; default derives from steps (any error -> error; any active or pending -> running; all success -> done). */
  state?: "running" | "done" | "error";
  /** Start collapsed when false. Default true. */
  defaultExpanded?: boolean;
  className?: string;
}

type PlanState = NonNullable<AgentPlanningProps["state"]>;

const NO_STEPS: PlanStep[] = [];
/** Delay between rows that arrive together. */
const STAGGER_MS = 60;

const DEFAULT_TITLE: Record<PlanState, string> = {
  running: "Working on it",
  done: "Done",
  error: "Stopped",
};

const STATUS_LABEL: Record<PlanStepStatus, string> = {
  pending: "Not started",
  active: "In progress",
  success: "Done",
  error: "Failed",
};

/** Hidden state word before a custom header title: the header icon alone is not announced. */
const STATE_LABEL: Record<PlanState, string> = { running: "In progress", done: "Done", error: "Failed" };

/** Any error -> error; any active or pending (or no steps yet) -> running; all success -> done. */
function deriveState(steps: PlanStep[]): PlanState {
  if (steps.some((s) => s.status === "error")) return "error";
  if (steps.length === 0 || steps.some((s) => s.status !== "success")) return "running";
  return "done";
}

/** What the polite live region says: the running step, the failed step, or "Done". */
function announcement(steps: PlanStep[], state: PlanState): string {
  if (state === "done") return "Done";
  const at = steps.findIndex((s) => s.status === (state === "error" ? "error" : "active"));
  if (at < 0) return state === "error" ? "Stopped" : "";
  const where = `Step ${at + 1} of ${steps.length}`;
  return state === "error" ? `${where} failed: ${steps[at].title}` : `${where}: ${steps[at].title}`;
}

/** Smooth height change via grid rows 1fr <-> 0fr. Closed content is inert: no focus, hidden from assistive tech. */
type CollapseProps = { open: boolean; id?: string; className?: string; children: React.ReactNode };

function Collapse({ open, id, className, children }: CollapseProps) {
  return (
    <div
      id={id}
      inert={!open}
      className={cn(
        "grid transition-[grid-template-rows,opacity] duration-300 ease-out motion-reduce:transition-none",
        open ? "grid-rows-[1fr] opacity-100" : "grid-rows-[0fr] opacity-0",
        className,
      )}
    >
      <div className="min-h-0 overflow-hidden">{children}</div>
    </div>
  );
}

function HeaderIcon({ state }: { state: PlanState }) {
  if (state === "running") return <Loader2 className="size-4 text-primary motion-safe:animate-spin" />;
  if (state === "error") {
    return (
      <AlertTriangle className="size-4 text-destructive animate-in fade-in zoom-in-50 duration-200 motion-reduce:animate-none" />
    );
  }
  return (
    <span className="flex size-5 items-center justify-center rounded-full bg-brand-soft text-primary animate-in fade-in zoom-in-50 duration-200 motion-reduce:animate-none">
      <Check strokeWidth={3} className="size-3" />
    </span>
  );
}

function StepIcon({ status, icon }: { status: PlanStepStatus; icon?: React.ReactNode }) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "flex size-[22px] shrink-0 items-center justify-center rounded-full border-[1.5px] border-transparent transition-colors duration-300 [&_svg]:size-3",
        status === "pending" && "border-foreground/20 bg-card text-muted-foreground",
        (status === "active" || status === "success") && "bg-brand-soft text-primary",
        status === "error" && "bg-signal-red-bg text-destructive",
      )}
    >
      {status === "success" ? (
        <Check strokeWidth={3} className="animate-in fade-in zoom-in-50 duration-200 motion-reduce:animate-none" />
      ) : status === "active" ? (
        <Loader2 strokeWidth={2.5} className="motion-safe:animate-spin" />
      ) : status === "error" ? (
        (icon ?? <X strokeWidth={3} />)
      ) : (
        icon
      )}
    </span>
  );
}

/**
 * Collapsible step timeline for an agent run: header with status icon, title and elapsed time, a progress
 * hairline while running, and one row per step (check / spinner / ring, title, tag chips, muted detail line).
 * Steps may be appended or change status at any time; rows that arrive together enter staggered.
 */
export function AgentPlanning({
  title,
  steps = NO_STEPS,
  elapsed,
  state: stateProp,
  defaultExpanded = true,
  className,
}: AgentPlanningProps): React.JSX.Element {
  const uid = React.useId();
  const [open, setOpen] = React.useState(defaultExpanded);
  const [openSteps, setOpenSteps] = React.useState<Record<string, boolean>>({});
  // Rows stagger from this index; it moves to the end once the newest row has finished entering,
  // so later arrivals start at once (or stagger among themselves) instead of waiting index * 60ms.
  const [staggerFrom, setStaggerFrom] = React.useState(0);

  const state = stateProp ?? deriveState(steps);
  const running = state === "running";
  const total = steps.length;
  const completed = steps.filter((s) => s.status === "success").length;
  const progress = total > 0 ? (completed / total) * 100 : 0;
  const heading = title ?? DEFAULT_TITLE[state];
  const bodyId = `${uid}-body`;
  // The live region mounts empty and fills one render later: text already present when a region
  // is inserted is not announced, and the parent usually mounts the card with step 1 already active.
  const liveMessage = React.useDeferredValue(announcement(steps, state), "");

  const isStepOpen = (step: PlanStep) => openSteps[step.id] ?? step.defaultExpanded ?? false;
  const toggleStep = (step: PlanStep) =>
    setOpenSteps((prev) => ({ ...prev, [step.id]: !(prev[step.id] ?? step.defaultExpanded ?? false) }));

  return (
    <div
      data-slot="agent-planning"
      data-state={state}
      className={cn(
        // pb-3.5 + the header's mb-1.5 keep 20px under a collapsed header; the body adds pb-1.5 back when open.
        "w-full rounded-2xl border bg-card px-5 pb-3.5 pt-5 text-card-foreground animate-in fade-in slide-in-from-bottom-2 duration-300 motion-reduce:animate-none",
        className,
      )}
    >
      <button
        type="button"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setOpen((v) => !v)}
        className="mb-1.5 flex w-full cursor-pointer items-start gap-2.5 rounded-md text-left"
      >
        <span aria-hidden="true" className="flex h-[22px] w-5 shrink-0 items-center justify-center">
          <HeaderIcon state={state} />
        </span>
        <span
          key={heading}
          className="min-w-0 flex-1 break-words text-[15px] font-medium leading-[22px] text-foreground animate-in fade-in duration-300 motion-reduce:animate-none"
        >
          {title != null && <span className="sr-only">{STATE_LABEL[state]}: </span>}
          {heading}
        </span>
        {elapsed && (
          <span className="shrink-0 text-[13px] leading-[22px] text-muted-foreground tabular-nums animate-in fade-in duration-300 motion-reduce:animate-none">
            {elapsed}
          </span>
        )}
        {!open && <ChevronRight aria-hidden="true" className="mt-[3px] size-4 shrink-0 text-muted-foreground" />}
      </button>

      <p aria-live="polite" aria-atomic="true" className="sr-only">
        {liveMessage}
      </p>

      {/* The body bleeds 6px past the content (-mx-1.5 / px-1.5 / pb-1.5) so the clipping needed for the
          height transition does not cut the focus outline of step rows that reach the right or bottom edge. */}
      <Collapse id={bodyId} open={open} className="-mx-1.5">
        <div className="px-1.5 pb-1.5">
          {/* Hairline divider. While running, a 2px track sits on it: determinate fill plus a sliding glint. */}
          <div aria-hidden="true" className="relative mt-1.5 h-px bg-border">
            <div
              className={cn(
                "absolute inset-x-0 -top-px h-0.5 overflow-hidden rounded-full bg-brand-soft transition-opacity duration-500 motion-reduce:transition-none",
                // When the run ends, hold the track briefly so the fill is seen reaching its end, then fade.
                running ? "opacity-100" : "opacity-0 delay-300",
              )}
            >
              <div
                className="h-full rounded-full bg-primary transition-[width] duration-500 ease-out motion-reduce:transition-none"
                style={{ width: `${progress}%` }}
              />
              {running && <div className="agent-glint" />}
            </div>
          </div>

          {total > 0 && (
            // Explicit role: Safari/VoiceOver drops list semantics from lists styled list-style: none.
            <ol role="list" className="pt-3.5">
              {steps.map((step, i) => {
                const last = i === total - 1;
                const expandable = step.content != null;
                const expanded = expandable && isStepOpen(step);
                const contentId = `${uid}-step-${i}`;
                const rowClass = "flex w-full items-start gap-2 text-left text-[15px] leading-[22px] text-foreground";
                const row = (
                  <>
                    <span className="min-w-0 flex-1">
                      <span className="sr-only">{STATUS_LABEL[step.status]}: </span>
                      <span
                        className={cn(
                          step.status === "active" && "agent-shimmer-text",
                          step.status === "error" && "text-destructive",
                        )}
                      >
                        {step.title}
                      </span>
                      {/* A real space before each chip keeps title and tags separate words for screen readers and copy. */}
                      {step.tags?.map((tag, t) => (
                        <React.Fragment key={`${t}-${tag}`}>
                          {" "}
                          <span className="ml-1 inline-block whitespace-nowrap rounded-md bg-brand-soft px-1.5 py-0.5 text-[12px] font-medium leading-4 text-brand-ink">
                            {tag}
                          </span>
                        </React.Fragment>
                      ))}
                    </span>
                    {step.duration && (
                      <span className="shrink-0 text-[13px] text-muted-foreground tabular-nums">{step.duration}</span>
                    )}
                    {expandable && (
                      <ChevronRight
                        aria-hidden="true"
                        className={cn(
                          "mt-[3px] size-4 shrink-0 text-muted-foreground transition-transform duration-200 group-hover:text-foreground motion-reduce:transition-none",
                          expanded && "rotate-90",
                        )}
                      />
                    )}
                  </>
                );

                return (
                  <li
                    key={step.id}
                    style={{ animationDelay: `${Math.max(0, i - staggerFrom) * STAGGER_MS}ms` }}
                    onAnimationEnd={(e) => {
                      if (e.target === e.currentTarget && last) setStaggerFrom(total);
                    }}
                    className="flex gap-3 animate-in fade-in slide-in-from-top-2 duration-300 fill-mode-both motion-reduce:animate-none"
                  >
                    <div className="flex flex-col items-center">
                      <StepIcon status={step.status} icon={step.icon} />
                      {!last && (
                        <span
                          aria-hidden="true"
                          className={cn(
                            "my-1 w-px flex-1 transition-colors duration-500 animate-in fade-in motion-reduce:animate-none",
                            step.status === "success" ? "bg-primary/60" : "bg-border",
                          )}
                        />
                      )}
                    </div>

                    <div
                      className={cn(
                        "min-w-0 flex-1 break-words transition-opacity duration-300",
                        step.status === "pending" && "opacity-50",
                        !last && "pb-4",
                      )}
                    >
                      {expandable ? (
                        <button
                          type="button"
                          aria-expanded={expanded}
                          aria-controls={contentId}
                          onClick={() => toggleStep(step)}
                          className={cn(rowClass, "group cursor-pointer rounded-sm")}
                        >
                          {row}
                        </button>
                      ) : (
                        <div className={rowClass}>{row}</div>
                      )}
                      <Collapse open={step.detail != null}>
                        <div className="text-[13px] leading-5 text-muted-foreground">{step.detail}</div>
                      </Collapse>
                      {expandable && (
                        <Collapse id={contentId} open={expanded}>
                          <div className="pt-2 text-[13px] leading-5 text-muted-foreground">{step.content}</div>
                        </Collapse>
                      )}
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
        </div>
      </Collapse>
    </div>
  );
}

export default AgentPlanning;
