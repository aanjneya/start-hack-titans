# DATA FORMAT v1: how agents hand things on (binding for every agent)

> Why: the Launch Planner (Jivi) and the human decide based on what the other agents report. So every problem,
> risk, missing answer and warning arrives in **one shape: a Finding**. Decision cards (Planner → human) already have
> their own fixed shape (`DecisionCard` in `API_CONTRACT.ts`). Code: `backend/core/models.py` (`Finding`, `Impact`,
> `SuggestedAction`), validated automatically by `Agent.raise_issue(...)`. Bad findings are rejected.

## 1. The Finding (stored in `issues`, served by `GET /api/issues`)
| Field | Required | Meaning |
|---|---|---|
| `type` | ✓ | what kind of problem, snake_case (`rev_blocked`, `tracker_mismatch`, `stale_feed`, `single_source`, `short_shipment`, `bank_change`, …) |
| `kind` | ✓ | routing: `missing_info` → Chaser · `copies_at_risk` → Planner · `human_check` → a person |
| `severity` | ✓ | 1 info · 2 watch · 3 answer today · 4 copies at risk · 5 line stops / launch at risk now |
| `summary` | ✓ | **one line** (≤160 chars) a manager understands, e.g. "Rev B sleeves can't be packed: 40,000 Standard copies short" |
| `key` | ✓ | stable id of the problem (e.g. `CMP-GAM-004/B`): same key = same finding, updated not duplicated |
| `reason` | | why it matters, 1–3 sentences |
| `subject_type` / `subject_id` | | `part` · `supplier` · `shipment` · `po` · `product` · `document` · `drop` (inferred from the ids below if empty) |
| `part_id` `supplier_id` `shipment_id` `po_number` `product_id` | | our IDs only (`CMP-…`, `SUP-…`, `SHP-…`, `4500…`, `FG-…`) |
| `impact` | | `{product_id, copies_at_risk, hours_buffer, due_by (ISO time), cost_eur, note}`: **numbers from the data or empty, never guessed** |
| `suggested_actions` | | list of `{type, label, target, owner, cost_eur, copies_gained}`; `type` ∈ `email` `expedite` `second_source` `safety_stock` `reallocate` `use_old_revision` `block_stock` `human_review` `other`; `owner` ∈ `chaser` `planner` `human` |
| `evidence` | ✓ (≥1 when possible) | list of `{path, locator, quote}`: the file and row/page that prove it |
| `confidence` | | 0–1, how sure the agent is |
| `status` | (set by the system) | `open` → `routed`/`asked` → `resolved` (or `reopened`) |

## 2. Example (what the Planner receives)
```json
{"type": "rev_blocked", "kind": "copies_at_risk", "severity": 4,
 "summary": "Rev B sleeves can't be packed: 40,000 Standard copies short",
 "reason": "ECN-2026-118 makes rev C mandatory from today; stock and SHP-2026-04313 are rev B.",
 "subject_type": "part", "subject_id": "CMP-GAM-004", "supplier_id": "SUP-0028", "product_id": "FG-GH6-STD",
 "impact": {"product_id": "FG-GH6-STD", "copies_at_risk": 40000, "hours_buffer": 6.5, "due_by": "2026-11-21T22:00:00+01:00"},
 "suggested_actions": [
   {"type": "email", "label": "Ask Karton Nowak which revision SHP-2026-04313 is", "target": "SUP-0028", "owner": "chaser"},
   {"type": "expedite", "label": "Order the rush rev C run (offer of 21.11.)", "target": "SUP-0028", "owner": "planner", "copies_gained": 40000}],
 "evidence": [{"path": "soony/master/bom_delta_2026-11-21.csv", "locator": "ECN-2026-118", "quote": ""}],
 "confidence": 0.9}
```
(The numbers above are an illustration of the shape, not real results.)

## 3. Who produces / consumes what
| Agent | Produces | Consumes |
|---|---|---|
| 1 Watcher | drops (`files.changed`) | – |
| 2 Ingest | data rows (only writer) + `human_check` findings for unreadable/unmatched data | files, supplier replies |
| 3 Reconciler | findings for every contradiction/gap (`missing_info`, `copies_at_risk`, `human_check`) | `truth.updated`, `risk.updated` |
| 4 Chaser | emails (outbox), replies (inbox); updates the finding's status to `asked` | `missing_info` findings, `email.requested` |
| 6 Risk Rater | `RiskScore` rows (grade 1–6 + reasons) **and** a finding (with suggested actions) for every grade ≥ 5 | `truth.updated` |
| 5 Planner | `DecisionCard`s (options with € vs copies, approver) + forecasts | `copies_at_risk` findings, risk scores, data |

## 4. Rules
1. Use `self.raise_issue(...)`; never write `issues` rows by hand.
2. One finding per problem; the `key` must not change between runs (re-checks update it).
3. `summary` is for a manager: plain words, the number that matters, no jargon.
4. Every finding with severity ≥ 3 should have at least one `suggested_action` (office hours: "I need the risk, but I want the solution").
5. Only our IDs in the id fields; supplier names/part numbers are translated by Ingest's aliases first.
6. Events between agents carry `_meta = {from, run_id, ts, v}` automatically (`Agent.emit`).
7. Changing this format = add fields only, bump `FINDING_SCHEMA_VERSION`, update this file + `API_CONTRACT.ts`, log in DECISIONS.md.
