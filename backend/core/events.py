"""Event names agents use to hand work to each other (see .brain/tasks/README.md loop)."""

FILES_CHANGED = "files.changed"  # Watcher -> Ingest. payload: {added, changed, removed, label, data_root}
TRUTH_UPDATED = "truth.updated"  # Ingest -> Reconciler, Risk. payload: {tables, files}
ISSUE_RAISED = "issue.raised"  # Reconciler -> Chaser/Planner/human. payload: {issue_id, kind}
RISK_UPDATED = "risk.updated"  # Risk -> Reconciler/Planner. payload: {entity_ids}
DECISION_CREATED = "decision.created"  # Planner -> UI. payload: {decision_id}
DECISION_APPROVED = "decision.approved"  # UI/API -> Planner (execute). payload: {decision_id, option, by}
DECISION_REJECTED = "decision.rejected"  # UI/API -> Planner. payload: {decision_id, by}
EMAIL_REQUESTED = "email.requested"  # Planner -> Chaser. payload: {supplier_id, issue_id?, decision_id?, ask}
REPLY_RECEIVED = "reply.received"  # Chaser -> Ingest. payload: {supplier_id, outbox_id, text}
FORECAST_UPDATED = "forecast.updated"  # Planner -> UI. payload: {forecast_ids}

ALL = [
    FILES_CHANGED, TRUTH_UPDATED, ISSUE_RAISED, RISK_UPDATED, DECISION_CREATED,
    DECISION_APPROVED, DECISION_REJECTED, EMAIL_REQUESTED, REPLY_RECEIVED, FORECAST_UPDATED,
]
