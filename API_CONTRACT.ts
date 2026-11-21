/**
 * API_CONTRACT.ts
 *
 * Single source of truth for shared API endpoints, request/response payloads,
 * and domain types across frontend and backend.
 *
 * Rule: Update this contract FIRST before implementing or changing integrations.
 * Backend: Python/FastAPI in backend/ (models in backend/core/models.py must match these types).
 */

export interface ApiError {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

export interface ApiResponse<T> {
  ok: boolean;
  data?: T;
  error?: ApiError;
  mocked?: boolean;
}

export interface HealthCheckResponse {
  status: 'ok' | 'degraded';
  mockMode: boolean;
  timestamp: string;
  dataRoot: string;
  agents: string[];
}

/** Where a fact or finding comes from (path is relative to the portal data root). */
export interface Evidence {
  path: string;
  locator: string; // e.g. "row 12", "page 2"
  sha256: string;
  quote: string;
}

export type IssueKind = 'missing_info' | 'copies_at_risk' | 'human_check';

/** FINDING FORMAT v1 (.brain/DATA_FORMAT.md): the one shape every agent uses to hand a problem on. */
export type SubjectType = 'part' | 'supplier' | 'shipment' | 'po' | 'product' | 'document' | 'drop';
export type ActionType =
  | 'email' | 'expedite' | 'second_source' | 'safety_stock' | 'reallocate'
  | 'use_old_revision' | 'block_stock' | 'human_review' | 'other';

export interface Impact {
  product_id: string;
  copies_at_risk: number | null;
  hours_buffer: number | null;
  due_by: string | null; // ISO datetime
  cost_eur: number | null;
  note: string;
}

export interface SuggestedAction {
  type: ActionType;
  label: string;
  target: string;
  owner: 'chaser' | 'planner' | 'human' | '';
  cost_eur: number | null;
  copies_gained: number | null;
}
export type IssueStatus = 'open' | 'routed' | 'asked' | 'resolved' | 'reopened';

export interface Issue {
  id: number;
  fingerprint: string;
  type: string; // e.g. tracker_mismatch, stale_feed, rev_blocked, single_source
  kind: IssueKind;
  severity: number; // 1 info · 2 watch · 3 answer today · 4 copies at risk · 5 line stops
  summary: string; // one-line headline for a manager
  reason: string;
  subject_type: SubjectType | '';
  subject_id: string;
  part_id: string;
  supplier_id: string;
  shipment_id: string;
  po_number: string;
  product_id: string;
  impact: Impact | Record<string, never>;
  suggested_actions: SuggestedAction[];
  confidence: number | null;
  schema_version: number;
  evidence: Evidence[];
  status: IssueStatus;
  raised_by: string;
  created_at: string;
  updated_at: string;
}

export interface DecisionOption {
  label: string;
  description: string;
  cost_eur: number | null;
  copies_gained: number | null;
  actions: Record<string, unknown>[];
  risk_note: string;
  /** Planner options carry the chosen option's forecast and policy result. */
  remaining_shortfall?: number | null;
  approval_value_eur?: number | null;
  approvers?: string[];
  approval_roles?: string[];
  needs_approval?: boolean;
  available?: boolean;
  evidence?: Evidence[];
}

export type DecisionStatus = 'proposed' | 'approved' | 'rejected' | 'executed' | 'outdated';

export interface DecisionCard {
  id: number;
  issue_ids: number[];
  title: string;
  summary: string; // one line for the exec: risk -> recommended action
  problem: string;
  reasoning: string; // full "why", shown on demand
  evidence: Evidence[];
  options: DecisionOption[];
  recommended: number | null; // index into options
  approver: string; // from the customer's approval matrix
  approver_reason: string;
  needs_approval: boolean; // false = executed automatically (autonomy switch "auto")
  decide_by: string | null;
  status: DecisionStatus;
  chosen_option: number | null;
  decided_by: string;
  decided_at: string | null;
  created_by: string;
  created_at: string;
}

export interface Forecast {
  id: number;
  computed_at: string;
  product_id: string;
  edition: string;
  deadline: string | null;
  still_to_pack: number | null;
  projected_packed: number | null;
  shortfall: number | null;
  limiting_part: string;
  details: Record<string, unknown>;
}

export interface RiskScore {
  id: number;
  entity_type: 'part' | 'supplier';
  entity_id: string;
  grade: number; // 1 safe .. 6 critical
  rating: string; // AAA..CCC
  reasons: { text: string; evidence?: Evidence[] }[];
  computed_at: string;
}

export interface AgentLogEntry {
  id: number;
  ts: string;
  run_id: string;
  agent: string;
  action: string; // e.g. handle:files.changed, raise_issue, llm_call, error, todo
  detail: string;
  evidence: Evidence[];
  data: Record<string, unknown>;
}

export interface OutboxMessage {
  id: number;
  supplier_id: string;
  issue_id: number | null;
  decision_id: number | null;
  to_email: string;
  language: string;
  subject: string;
  body: string;
  body_en: string;
  status: 'draft' | 'approved' | 'sent' | 'replied' | 'failed' | 'escalated';
  created_at: string;
  sent_at: string | null;
  message_id: string; // Message-ID we sent; replies are matched by In-Reply-To
  run_id: string;
}

export interface CustomerSetup {
  demo_buyer?: string;
  customer: string;
  autonomy: { mode: 'control' | 'auto'; auto_execute_below_eur: number };
  approval_matrix: { below_eur: number | null; approvers: string[] }[];
}

export interface FieldChange {
  id: number;
  ts: string;
  run_id: string;
  table_name: string;
  row_id: number;
  entity_key: string;
  field: string;
  old_value: string | null;
  new_value: string | null;
  change_type: 'fill' | 'overwrite';
  source: string; // file or email the new value came from
  agent: string;
  reason: string;
  confidence: number | null;
  issue_id: number | null;
}

export interface InboxMessage {
  id: number;
  supplier_id: string;
  outbox_id: number | null;
  issue_id: number | null;
  from_email: string;
  subject: string;
  body: string;
  body_en: string;
  language: string;
  received_at: string;
  processed: boolean;
  result: Record<string, unknown>;
}

/** Structured supplier advice on the approved PO line. A supplier cannot release the receiving dock. */
export interface DeliveryAdvice {
  po_number: string;
  po_line: number;
  shipment_id: string;
  revision: string;
  quantity: number;
  arrival: string; // ISO timestamp with timezone at the receiving site
  supersedes_inbox_id?: number; // explicit correction to earlier accepted advice
}

export interface SupplierQuote extends DeliveryAdvice {
  mode: 'expedite' | 'air' | 'pickup';
  incremental_cost_eur: number; // all-in extra charge to the existing PO
  valid_until: string;
}

export interface ReplyRequest {
  quote?: SupplierQuote; // conditional alternative; mutually exclusive with delivery
  delivery?: DeliveryAdvice; // requires matching decision-linked outbox_id
  supplier_id: string;
  body: string;
  issue_id?: number;
  outbox_id?: number;
  from_email?: string;
  subject?: string;
  language?: string;
}

export interface RunResult {
  run_id: string;
  drop_id: number;
  label: string;
  steps: number;
  added: number;
  changed: number;
  removed: number;
  open_issues: number;
}

export interface RunRequest {
  added?: string[]; // omit everything = reprocess the whole data folder
  changed?: string[];
  removed?: string[];
  label?: string;
  reset?: boolean;
}

export interface DecideRequest {
  by: string;
  option?: number; // default: recommended; invalid indexes return 422, stale cards 409
  approvals?: string[]; // additional signers for jointly approved options
}

/** One clean data table (purchase_orders, order_confirmations, goods_receipts, stock, shipments, ...). */
export interface TruthTableInfo {
  table: string;
  rows: number;
}

/**
 * Route registry — keep endpoint paths and HTTP methods in sync here.
 * Backend base URL in dev: http://localhost:8000
 */
export const API_ROUTES = {
  HEALTH: '/api/health', // GET -> HealthCheckResponse
  RUN: '/api/run', // POST RunRequest -> RunResult
  REPLAY: '/api/replay', // POST ?upto=N -> RunResult[]
  REPLAY_STEP: '/api/replay/step', // POST -> next real drop through the agents (demo: "a new drop arrives")
  WATCH_CHECK: '/api/watch/check', // POST -> live portal check now (no-op in MOCK mode)
  DROPS: '/api/drops', // GET
  ISSUES: '/api/issues', // GET ?status= -> Issue[]
  CONFLICTS: '/api/conflicts', // GET
  FORECAST: '/api/forecast', // GET -> Forecast[]
  DECISIONS: '/api/decisions', // GET ?status= -> DecisionCard[]
  DECISION_APPROVE: '/api/decisions/:id/approve', // POST DecideRequest
  DECISION_REJECT: '/api/decisions/:id/reject', // POST DecideRequest
  TRACE: '/api/trace', // GET ?limit=&agent=&run_id= -> AgentLogEntry[]
  RISK: '/api/risk', // GET -> RiskScore[]
  OUTBOX: '/api/outbox', // GET -> OutboxMessage[]
  PART_JOURNEY: '/api/part-journey', // GET ?material_id= -> rows of the part_journey view
  SETUP: '/api/setup', // GET -> CustomerSetup (approval matrix + autonomy switch)
  CHANGES: '/api/changes', // GET ?table= -> FieldChange[] (change log of the source of truth)
  INBOX: '/api/inbox', // GET -> InboxMessage[]; POST ReplyRequest -> feeds a supplier reply into the loop
  TRUTH: '/api/truth', // GET -> TruthTableInfo[] (clean data tables built by Watcher + Ingest, with row counts)
  TRUTH_ROWS: '/api/truth/:table', // GET ?limit=&q= -> rows of one data table (source = first file, last_source = file that changed it last)
} as const;
