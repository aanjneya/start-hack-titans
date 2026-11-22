"""Deterministic packing projection. Times are site-local; quantities refer to finished copies."""
from __future__ import annotations

import math
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..core.models import Forecast, LaunchStatus


@dataclass
class Plan:
    as_of: datetime
    deadline: datetime
    shifts: list[tuple[datetime, datetime]]
    editions: list[LaunchStatus]  # highest priority first
    bom: dict[str, dict[str, float]]
    stock: Counter
    arrivals: dict[tuple, tuple[datetime, str, float]] = field(default_factory=dict)
    lots: list[dict] = field(default_factory=list)
    consumption: list[dict] = field(default_factory=list)
    inbound: list[dict] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    shipment_checks: dict[str, list[dict]] = field(default_factory=dict)
    line_balances: dict[tuple, float] = field(default_factory=dict)
    material_names: dict[str, str] = field(default_factory=dict)
    launch_priorities: dict[str, dict] = field(default_factory=dict)


def simulate(plan: Plan, overrides=None, *, priority=None) -> dict[str, Forecast]:
    """Replace a delivery by its physical key when evaluating a what-if; never add it twice."""
    arrivals = dict(plan.arrivals)
    arrivals.update(overrides or {})
    remaining_by_line = plan.line_balances.copy()
    timeline = []
    for key, (at, part, qty) in sorted(arrivals.items(), key=lambda item: item[1][0]):
        line = key[:3]
        if line in remaining_by_line:
            qty = min(qty, remaining_by_line[line])
            remaining_by_line[line] -= qty
        if qty > 0:
            timeline.append((at, part, qty))
    editions = priority or plan.editions
    stock = plan.stock.copy()
    packed = Counter()
    hourly = defaultdict(list)
    blockers = defaultdict(Counter)
    arrival_idx = 0
    hours = 0.0
    partial = Counter()

    for start, end in plan.shifts:
        now = max(plan.as_of, start)
        end = min(plan.deadline, end)
        # A truck at 13:30 can supply the second half of an hour.
        while now < end:
            while arrival_idx < len(timeline) and timeline[arrival_idx][0] <= now:
                _, part, qty = timeline[arrival_idx]
                stock[part] += qty
                arrival_idx += 1
            stop = min(end, now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1))
            if arrival_idx < len(timeline):
                stop = min(stop, timeline[arrival_idx][0])
            duration = (stop - now).total_seconds() / 3600
            hours += duration
            reserve = Counter()
            for row in editions:
                pid = row.product_id
                parts = plan.bom.get(pid, {})
                remain = max(0, (row.still_to_pack or 0) - packed[pid])
                work = partial[pid] + (row.rate_per_hour or 0) * duration
                capacity = min(remain, math.floor(work + 1e-9))
                if (row.line_status or "").lower() in {"stopped", "closed", "down"}:
                    capacity = 0
                cover = {part: math.floor(max(0, stock[part] - reserve[part]) / per)
                         for part, per in parts.items() if per > 0}
                units = min(capacity, min(cover.values(), default=0))
                partial[pid] = (max(0.0, work - math.floor(work + 1e-9))
                                if units < remain and min(cover.values(), default=0) > units else 0.0)
                if units < capacity:
                    for part, value in cover.items():
                        if value == units:
                            blockers[pid][part] += capacity - units
                for part, per in parts.items():
                    stock[part] -= units * per
                    # Protect stock needed by the higher-priority edition's outstanding orders.
                    reserve[part] += (remain - units) * per
                packed[pid] += units
                hourly[pid].append({"from": now.isoformat(), "to": stop.isoformat(), "copies": units})
            now = stop
    results = {}
    for row in editions:
        pid = row.product_id
        known = (row.packed is not None and row.still_to_pack is not None
                 and row.rate_per_hour is not None and bool(plan.bom.get(pid)))
        gap = max(0, row.still_to_pack - packed[pid]) if known else None
        limit = blockers[pid].most_common(1)
        part = limit[0][0] if gap and limit else ""
        results[pid] = Forecast(product_id=pid, edition=row.edition, deadline=plan.deadline,
                                still_to_pack=row.still_to_pack,
                                projected_packed=row.packed + packed[pid] if known else None,
                                shortfall=gap, limiting_part=part,
                                details={"as_of": plan.as_of.isoformat(), "working_hours": hours,
                                         "rate_per_hour": row.rate_per_hour, "hourly": hourly[pid],
                                         "limiting_part_name": plan.material_names.get(part),
                                         "eligible_stock_at_snapshot_time": {part: plan.stock[part]
                                             for part in plan.bom.get(pid, {})},
                                         "consumption_since_stock_count": [x for x in plan.consumption
                                             if x["part"] in plan.bom.get(pid, {})],
                                         "lots": [x for x in plan.lots if x["part"] in plan.bom.get(pid, {})],
                                         "inbound": [x for x in plan.inbound if x["part"] in plan.bom.get(pid, {})],
                                         "assumptions": plan.assumptions, "evidence": plan.evidence})
    return results
