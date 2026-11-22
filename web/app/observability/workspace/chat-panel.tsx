"use client";

// Right pane of the agent workspace: ask the agent a question, watch its steps run, read the answer.
import { ArrowUp, Loader2, Plus, Sparkles } from "lucide-react";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { AgentPlanning, type PlanStep } from "@/components/ui/agent-planning";
import { cn } from "@/lib/utils";
import { planRun, starterQuestions } from "./brain";
import type { AgentAnswer, AnswerBlock, AnswerCell, BrainContext, CellTone } from "./types";

interface RunView {
  title: string;
  steps: PlanStep[];
  state: "running" | "done" | "error";
  elapsed?: string;
  answer?: AgentAnswer;
  error?: string;
}

type Message = { id: number; role: "user"; text: string } | { id: number; role: "agent"; run: RunView };

/** How long each step stays visibly "active" at least, so the work reads as steps (real fetches run meanwhile). */
const PACE_MS = [640, 780, 700, 840, 720];

const wait = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

export function ChatPanel({
  ctx,
  agentLabel,
  onWorkingChange,
  onHighlight,
}: {
  ctx: BrainContext;
  /** "Ingest" or "Watcher". */
  agentLabel: string;
  onWorkingChange: (working: boolean) => void;
  onHighlight: (ids: string[]) => void;
}) {
  const [messages, setMessages] = useState<Message[]>([]);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const busyRef = useRef(false);
  const ctxRef = useRef(ctx);
  const nextId = useRef(1);
  const threadRef = useRef<HTMLDivElement>(null);
  const contentRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const formRef = useRef<HTMLFormElement>(null);
  /** Follow new content while the reader is at the bottom; stop once they scroll up to read. */
  const pinned = useRef(true);

  useEffect(() => {
    ctxRef.current = ctx;
  }, [ctx]);

  const starters = useMemo(() => starterQuestions(ctx), [ctx]);

  // Steps, detail lines and answer blocks grow in over a few hundred ms: keep the newest in view.
  useEffect(() => {
    const thread = threadRef.current;
    const content = contentRef.current;
    if (!thread || !content) return;
    const follow = () => {
      if (pinned.current) thread.scrollTop = thread.scrollHeight;
    };
    const observer = new ResizeObserver(follow);
    observer.observe(content);
    return () => observer.disconnect();
  }, []);

  const onThreadScroll = useCallback(() => {
    const el = threadRef.current;
    if (el) pinned.current = el.scrollHeight - el.clientHeight - el.scrollTop < 48;
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !formRef.current?.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [menuOpen]);

  const updateRun = useCallback((id: number, change: (run: RunView) => RunView) => {
    setMessages((ms) => ms.map((m) => (m.id === id && m.role === "agent" ? { ...m, run: change(m.run) } : m)));
  }, []);

  const setStep = useCallback(
    (runId: number, stepId: string, patch: Partial<PlanStep>) =>
      updateRun(runId, (r) => ({ ...r, steps: r.steps.map((s) => (s.id === stepId ? { ...s, ...patch } : s)) })),
    [updateRun],
  );

  const ask = useCallback(
    async (raw: string) => {
      const question = raw.trim();
      if (!question || busyRef.current) return;
      busyRef.current = true;
      pinned.current = true;
      setBusy(true);
      setDraft("");
      setMenuOpen(false);
      if (inputRef.current) inputRef.current.style.height = "";
      // Hand focus back at the end only if the presenter was using the chat (never yank it from elsewhere).
      const refocus = !!formRef.current?.closest("[data-chat-panel]")?.contains(document.activeElement);
      onWorkingChange(true);
      onHighlight([]);

      const userId = nextId.current++;
      const runId = nextId.current++;
      const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      const started = performance.now();
      const elapsed = () => `${((performance.now() - started) / 1000).toFixed(1)}s`;

      try {
        let plan: ReturnType<typeof planRun>;
        try {
          plan = planRun(question, ctxRef.current);
        } catch (e) {
          setMessages((ms) => [
            ...ms,
            { id: userId, role: "user", text: question },
            {
              id: runId,
              role: "agent",
              run: {
                title: "Couldn't plan that question",
                state: "error",
                steps: [],
                error: e instanceof Error ? e.message : String(e),
              },
            },
          ]);
          return;
        }
        setMessages((ms) => [
          ...ms,
          { id: userId, role: "user", text: question },
          {
            id: runId,
            role: "agent",
            run: {
              title: plan.workingTitle,
              state: "running",
              steps: plan.steps.map((s) => ({ id: s.id, title: s.title, tags: s.tags, status: "pending" })),
            },
          },
        ]);

        for (const [i, step] of plan.steps.entries()) {
          setStep(runId, step.id, { status: "active" });
          let detail: string | undefined;
          try {
            [detail] = await Promise.all([step.run(), wait(reduce ? 120 : PACE_MS[i % PACE_MS.length])]);
          } catch (e) {
            const message = e instanceof Error ? e.message : String(e);
            setStep(runId, step.id, { status: "error", detail: message });
            updateRun(runId, (r) => ({
              ...r,
              state: "error",
              title: "Couldn't finish the check",
              elapsed: elapsed(),
              error: `${step.title} failed: ${message}. Is the backend running?`,
            }));
            return;
          }
          setStep(runId, step.id, { status: "success", detail });
        }
        let answer: AgentAnswer;
        try {
          answer = plan.compose();
        } catch (e) {
          updateRun(runId, (r) => ({
            ...r,
            state: "error",
            title: "Couldn't put the answer together",
            elapsed: elapsed(),
            error: e instanceof Error ? e.message : String(e),
          }));
          return;
        }
        updateRun(runId, (r) => ({ ...r, state: "done", title: answer.title, elapsed: elapsed(), answer }));
        onHighlight(answer.highlight);
      } finally {
        busyRef.current = false;
        setBusy(false);
        onWorkingChange(false);
        if (refocus) inputRef.current?.focus({ preventScroll: true });
      }
    },
    [onHighlight, onWorkingChange, setStep, updateRun],
  );

  const placeholder = `Ask the ${agentLabel} agent…`;

  return (
    <div data-chat-panel className="flex h-full min-h-0 flex-col">
      <div
        ref={threadRef}
        role="log"
        aria-label={`Conversation with the ${agentLabel} agent`}
        onScroll={onThreadScroll}
        className="min-h-0 flex-1 overflow-y-auto px-5 pb-4 pt-6 sm:px-7"
      >
        <div ref={contentRef} className="flex min-h-full flex-col">
        {messages.length === 0 ? (
          <EmptyState agentLabel={agentLabel} starters={starters} onAsk={ask} disabled={busy} />
        ) : (
          <div className="space-y-6">
            {messages.map((m) =>
              m.role === "user" ? (
                <div key={m.id} className="flex justify-end animate-in fade-in slide-in-from-bottom-2 duration-300">
                  <p className="max-w-[88%] whitespace-pre-wrap rounded-[20px] bg-brand-soft px-5 py-3 text-[15.5px] leading-snug text-foreground">
                    {m.text}
                  </p>
                </div>
              ) : (
                <div key={m.id} className="space-y-5">
                  <AgentPlanning title={m.run.title} steps={m.run.steps} state={m.run.state} elapsed={m.run.elapsed} />
                  {m.run.answer && <AnswerView answer={m.run.answer} onAsk={ask} disabled={busy} />}
                  {m.run.error && (
                    <p className="rounded-xl border border-signal-red/25 bg-signal-red-bg px-4 py-3 text-sm text-signal-red animate-in fade-in duration-300">
                      {m.run.error}
                    </p>
                  )}
                </div>
              ),
            )}
          </div>
        )}
        </div>
      </div>

      <form
        ref={formRef}
        onSubmit={(e) => {
          e.preventDefault();
          void ask(draft);
        }}
        className="relative mx-4 mb-4 rounded-2xl border bg-card px-3 pb-3 pt-3.5 shadow-[0_1px_3px_rgba(16,24,20,0.05)] transition-shadow focus-within:shadow-[0_0_0_3px_color-mix(in_oklab,var(--color-ring)_22%,transparent)] sm:mx-6 sm:mb-6"
      >
        <label htmlFor={`ask-${agentLabel}`} className="sr-only">
          {placeholder}
        </label>
        <textarea
          id={`ask-${agentLabel}`}
          ref={inputRef}
          rows={1}
          value={draft}
          placeholder={placeholder}
          onChange={(e) => {
            setDraft(e.target.value);
            e.target.style.height = "auto";
            e.target.style.height = `${Math.min(e.target.scrollHeight, 160)}px`;
          }}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void ask(draft);
            }
          }}
          className="block max-h-40 min-h-7 w-full resize-none bg-transparent px-2 text-[16px] leading-relaxed text-foreground outline-none placeholder:text-muted-foreground focus-visible:outline-none"
        />
        <div className="mt-3 flex items-center justify-between">
          <button
            type="button"
            aria-label="Suggested questions"
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((o) => !o)}
            className={cn(
              "flex size-9 items-center justify-center rounded-full text-foreground/70 outline-none transition-colors hover:bg-secondary hover:text-foreground focus-visible:ring-[3px] focus-visible:ring-ring/40",
              menuOpen && "bg-secondary text-foreground",
            )}
          >
            <Plus aria-hidden className={cn("size-5 transition-transform duration-200", menuOpen && "rotate-45")} />
          </button>
          <button
            type="submit"
            aria-label={busy ? `The ${agentLabel} agent is working` : "Send"}
            disabled={!draft.trim() || busy}
            className={cn(
              "flex size-10 items-center justify-center rounded-xl bg-primary text-primary-foreground outline-none transition-[background-color,opacity,transform] hover:bg-brand-ink focus-visible:ring-[3px] focus-visible:ring-ring/50 active:scale-95 disabled:pointer-events-none",
              busy ? "opacity-80" : "disabled:opacity-35",
            )}
          >
            {busy ? (
              <Loader2 aria-hidden className="size-5 motion-safe:animate-spin" />
            ) : (
              <ArrowUp aria-hidden className="size-5" strokeWidth={2.25} />
            )}
          </button>
        </div>

        {menuOpen && (
          <div
            role="menu"
            aria-label="Suggested questions"
            className="absolute bottom-full left-0 z-20 mb-2 w-[min(100%,24rem)] rounded-xl border bg-popover p-1.5 shadow-lg animate-in fade-in slide-in-from-bottom-1 duration-150"
          >
            {starters.map((q) => (
              <button
                key={q}
                type="button"
                role="menuitem"
                disabled={busy}
                onClick={() => void ask(q)}
                className="block w-full rounded-lg px-3 py-2 text-left text-sm text-foreground outline-none hover:bg-secondary focus-visible:bg-secondary disabled:opacity-50"
              >
                {q}
              </button>
            ))}
          </div>
        )}
      </form>
    </div>
  );
}

function EmptyState({
  agentLabel,
  starters,
  onAsk,
  disabled,
}: {
  agentLabel: string;
  starters: string[];
  onAsk: (q: string) => void;
  disabled: boolean;
}) {
  return (
    <div className="flex flex-1 flex-col justify-end gap-4 pb-2 animate-in fade-in duration-500">
      <div>
        <p className="flex items-center gap-2 text-[15px] font-semibold text-foreground">
          <span className="flex size-7 items-center justify-center rounded-full bg-brand-soft text-primary">
            <Sparkles aria-hidden className="size-4" />
          </span>
          Ask the {agentLabel} agent
        </p>
        <p className="mt-1.5 text-sm text-muted-foreground">
          It answers from the live truth store and shows every step it takes.
        </p>
      </div>
      <div className="flex flex-col items-start gap-2">
        {starters.map((q, i) => (
          <button
            key={q}
            type="button"
            disabled={disabled}
            onClick={() => onAsk(q)}
            style={{ animationDelay: `${120 + i * 70}ms` }}
            className="rounded-full border bg-card px-4 py-2 text-left text-sm text-foreground outline-none transition-colors animate-in fade-in slide-in-from-bottom-1 duration-300 fill-mode-both hover:border-primary/40 hover:bg-brand-soft focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:opacity-50"
          >
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}

function AnswerView({ answer, onAsk, disabled }: { answer: AgentAnswer; onAsk: (q: string) => void; disabled: boolean }) {
  return (
    <div className="space-y-4">
      {answer.blocks.map((block, i) => (
        <div
          key={i}
          className="animate-in fade-in slide-in-from-bottom-1 duration-500 fill-mode-both"
          style={{ animationDelay: `${i * 110}ms` }}
        >
          <Block block={block} />
        </div>
      ))}
      {answer.followUps.length > 0 && (
        <div
          className="flex flex-wrap gap-2 pt-1 animate-in fade-in duration-500 fill-mode-both"
          style={{ animationDelay: `${answer.blocks.length * 110}ms` }}
        >
          {answer.followUps.map((q) => (
            <button
              key={q}
              type="button"
              disabled={disabled}
              onClick={() => onAsk(q)}
              className="rounded-full border bg-card px-3.5 py-1.5 text-[13px] text-foreground outline-none transition-colors hover:border-primary/40 hover:bg-brand-soft focus-visible:ring-[3px] focus-visible:ring-ring/40 disabled:opacity-50"
            >
              {q}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

const TONE: Record<CellTone, string> = {
  stop: "text-signal-red",
  warn: "text-signal-amber",
  ok: "text-signal-green",
  muted: "text-muted-foreground",
  strong: "font-semibold text-foreground",
  ai: "font-medium text-ai",
};

const cellValue = (c: AnswerCell) => (typeof c === "object" ? c.value : c);
const cellTone = (c: AnswerCell) => (typeof c === "object" ? c.tone : undefined);

function Block({ block }: { block: AnswerBlock }) {
  if (block.kind === "text") {
    return (
      <p className="text-[15.5px] leading-[1.6] text-foreground">
        {block.lead && <strong className="font-semibold">{block.lead}</strong>}
        {block.lead && block.text ? " " : null}
        {block.text && <RichText text={block.text} />}
      </p>
    );
  }
  if (block.kind === "bullets") {
    return (
      <ul className="space-y-1.5 text-[15px] leading-relaxed text-foreground">
        {block.items.map((item, i) => (
          <li key={i} className="flex gap-2.5">
            <span aria-hidden className="mt-[0.6em] size-1.5 shrink-0 rounded-full bg-primary/60" />
            <span>
              <RichText text={item} />
            </span>
          </li>
        ))}
      </ul>
    );
  }
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-[15px]">
        <thead>
          <tr className="border-b border-border">
            {block.columns.map((c, i) => (
              <th
                key={i}
                scope="col"
                className={cn(
                  "whitespace-nowrap py-2 pr-3 text-[13px] font-medium text-muted-foreground last:pr-0",
                  c.align === "right" ? "text-right" : "text-left",
                )}
              >
                {c.label}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {block.rows.map((row, r) => (
            <tr key={r} className="border-b border-border/70 last:border-b-0">
              {row.map((cell, i) => {
                const tone = cellTone(cell);
                const right = block.columns[i]?.align === "right";
                // Numbers and short ids (e.g. a part number) stay on one line; long names wrap anywhere.
                const shortId = i === 0 && String(cellValue(cell)).length <= 16;
                return (
                  <td
                    key={i}
                    className={cn(
                      "py-3 pr-3 align-top last:pr-0",
                      right ? "whitespace-nowrap text-right tabular-nums" : "text-left",
                      !right && (shortId ? "whitespace-nowrap" : "[overflow-wrap:anywhere]"),
                      tone && TONE[tone],
                    )}
                  >
                    {cellValue(cell)}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Supports **bold** inside answer text. */
function RichText({ text }: { text: string }): ReactNode {
  const parts = text.split(/\*\*(.+?)\*\*/g);
  return parts.map((part, i) =>
    i % 2 === 1 ? (
      <strong key={i} className="font-semibold">
        {part}
      </strong>
    ) : (
      <Fragment key={i}>{part}</Fragment>
    ),
  );
}
