"""Lab inventory: deterministic snapshot serializer, alerts, and feasibility.

Design rule (same routing-away principle as the instrument parsers): the LLM
NEVER computes inventory state. AI routes, Python calculates.

  * ``build_inventory_snapshot`` renders compact pipe-delimited tables straight
    from Mongo — zero LLM calls, capped at INVENTORY_SNAPSHOT_TOKEN_CAP, whole
    sections (then whole rows, never a partial row) dropped by priority with
    alerts and open loans surviving longest. The scope note is assembled from
    the serializer's own bookkeeping, never from model prose.
  * ``check_feasibility`` walks open loans (inv_tx with no actualReturn) plus
    APPROVED reservations overlapping the requested window (half-open:
    ``res.start < end and res.end > start``) and returns a per-item verdict.
    The answer LLM only parses the natural-language request into
    ``list[ItemRequest]`` (``extract_feasibility_request``) and narrates the
    returned report — it never does the arithmetic.

Pure cores (``render_snapshot``, ``alerts_for``, ``compute_feasibility``) take
prefetched data + an explicit ``now`` so they are unit-testable without Mongo;
thin async wrappers do the fetching (the repo-wide pure-core/async-shell
pattern).
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from app.core import config

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Sections + deterministic scope inference
# ---------------------------------------------------------------------------
ITEMS = "ITEMS"
OPEN_LOANS = "OPEN LOANS"
RESERVATIONS = "RESERVATIONS"
PLAXIS = "PLAXIS SEATS"
ALERTS = "ALERTS"

# Render order in the snapshot.
SECTION_ORDER = (ITEMS, OPEN_LOANS, RESERVATIONS, PLAXIS, ALERTS)

# Cap enforcement drops whole sections in THIS order — alerts and open loans
# go last, per the owner's priority.
SECTION_DROP_ORDER = (PLAXIS, RESERVATIONS, ITEMS, OPEN_LOANS, ALERTS)

# Substring → section map for scope inference. Deterministic and additive: a
# query matching several groups gets several sections; a query matching none
# gets everything ("all"). Lowercase substring matching only — no LLM.
_SCOPE_KEYWORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    (ITEMS, ("inventory", "stock", "equipment", "consumable", "item", "own",
             "availab", "how many", "quantity", "where is", "location",
             "condition", "supplier", "serial", "manufacturer")),
    (OPEN_LOANS, ("checked out", "check out", "checkout", "who has", "borrow",
                  "loan", "overdue", "return", "due back", "out right now")),
    (RESERVATIONS, ("reserv", "book", "hold", "schedule", "window",
                    "next week", "next month")),
    (PLAXIS, ("plaxis", "seat", "license", "licence")),
    (ALERTS, ("alert", "overdue", "expir", "low stock", "min stock",
              "maintenance", "maint", "calibrat", "damaged", "missing",
              "pending approval", "attention")),
)


def _matched_sections(query: str) -> List[str]:
    q = (query or "").lower()
    return [section for section, keys in _SCOPE_KEYWORDS if any(k in q for k in keys)]


def infer_inventory_scope(query: str) -> str:
    """Which snapshot sections this query needs, as a comma-joined string.

    Returns "all" when nothing matches (an unrecognised inventory question
    still gets the full picture) and also for feasibility-ish phrasing, since
    a booking answer needs items + loans + reservations together.
    """
    matched = _matched_sections(query)
    if not matched:
        return "all"
    return ",".join(matched)


def query_mentions_inventory(query: str) -> bool:
    """Deterministic MIXED-mode hook: does this query touch inventory state?

    Pure keyword membership (no LLM): a MIXED turn matching any scope keyword
    group gets the snapshot APPENDED to its retrieved context — never
    replacing it — and is exempted from the answer cache (live state)."""
    return bool(_matched_sections(query))


def _parse_scope(scope: str) -> List[str]:
    """Resolve a scope string to render-ordered section names."""
    if not scope or scope.strip().lower() == "all":
        return list(SECTION_ORDER)
    wanted = {s.strip().upper() for s in scope.split(",") if s.strip()}
    resolved = [s for s in SECTION_ORDER if s.upper() in wanted]
    return resolved or list(SECTION_ORDER)


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _as_dt(value: Any) -> Optional[datetime]:
    """Coerce a stored date (datetime or ISO string) to datetime, else None."""
    if isinstance(value, datetime):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


def _fmt_dt(value: Any) -> str:
    dt = _as_dt(value)
    if dt is None:
        return ""
    if (dt.hour, dt.minute, dt.second) == (0, 0, 0):
        return dt.strftime("%Y-%m-%d")
    return dt.strftime("%Y-%m-%d %H:%M")


def _cell(value: Any) -> str:
    """One pipe-table cell: never empty (keeps columns aligned), never a pipe."""
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return _fmt_dt(value)
    s = str(value).replace("|", "/").strip()
    return s if s else "-"


def _row(cells: Sequence[Any]) -> str:
    return " | ".join(_cell(c) for c in cells)


def _est_tokens(text: str) -> int:
    # Same rough heuristic as the chat history cap (len // 4).
    return len(text) // 4


def _item_name(items_by_id: Dict[str, Dict], item_id: Any) -> str:
    doc = items_by_id.get(str(item_id))
    return (doc or {}).get("name") or str(item_id)


def _is_open_loan(tx: Dict[str, Any]) -> bool:
    return tx.get("type") == "checkout" and not tx.get("actualReturn")


def active_items(items: Sequence[Dict]) -> List[Dict]:
    """Archived items are out of circulation: excluded from the snapshot,
    alerts, KPIs and the feasibility walk (spec: archive instead of delete)."""
    return [i for i in items if str(i.get("status") or "").lower() != "archived"]


# ---------------------------------------------------------------------------
# Reserved is DERIVED, never stored.
#
# The bench (and the sheet it was transcribed from) wrote "Reserved" onto the
# item when an approved booking was created and nothing ever cleared it, so
# an item could read Reserved with no reservation behind it (LL-SEN-004 was
# seeded exactly that way). Here the item's status is computed from the live
# inv_res rows on every read: a non-denied reservation whose window has not
# ended makes an otherwise-available item Reserved; none makes a stored
# Reserved read Available. Deny, cancel, delete and expiry therefore need no
# clearing step — they simply stop being live rows. Any other status (Under
# maintenance, Borrowed, Missing, Archived...) is left alone: a broken item
# with a booking is still broken.
# ---------------------------------------------------------------------------
_RESERVED = "Reserved"
_AVAILABLE = "Available"
_RESERVABLE = ("available", "reserved")
_DEAD_RES = ("denied", "cancelled", "canceled")


def _live_reservation(r: Dict[str, Any], now: datetime) -> bool:
    if str(r.get("status") or "Pending").lower() in _DEAD_RES:
        return False
    end = _as_dt(r.get("end"))
    return end is None or end >= now


def derive_reserved(
    items: Sequence[Dict], reservations: Sequence[Dict], now: datetime
) -> List[Dict]:
    """Return COPIES of ``items`` with ``status`` derived from ``reservations``
    (see the note above). Pure; the input dicts are not mutated."""
    live: Dict[str, int] = {}
    for r in reservations:
        if _live_reservation(r, now):
            key = str(r.get("itemId"))
            live[key] = live.get(key) + 1 if key in live else 1
    out: List[Dict] = []
    for it in items:
        doc = dict(it)
        stored = str(doc.get("status") or "").lower()
        if stored in _RESERVABLE:
            doc["status"] = _RESERVED if live.get(str(doc.get("id"))) else _AVAILABLE
        out.append(doc)
    return out


def strip_stored_reserved(doc: Dict[str, Any]) -> None:
    """Write-path guard: a client (or an old backup) asking to STORE
    ``Reserved`` stores ``Available`` instead — the reservation rows decide
    what is read back. In place; no-op for every other status."""
    if str(doc.get("status") or "").lower() == _RESERVED.lower():
        doc["status"] = _AVAILABLE


def reconcile_status_plan(
    items: Sequence[Dict], reservations: Sequence[Dict], now: datetime
) -> List[Dict[str, Any]]:
    """Items whose STORED status disagrees with their reservations. Each row:
    ``{id, name, stored, derived, live}`` (live = count of backing rows).
    Stored-Reserved-with-nothing-behind-it is the stale case the one-shot
    script rewrites; stored-Available-with-live-rows is reported only (the
    read path already shows it as Reserved and the rows will expire)."""
    derived = {str(d.get("id")): d for d in derive_reserved(items, reservations, now)}
    live: Dict[str, int] = {}
    for r in reservations:
        if _live_reservation(r, now):
            key = str(r.get("itemId"))
            live[key] = live.get(key, 0) + 1
    plan: List[Dict[str, Any]] = []
    for it in items:
        key = str(it.get("id"))
        stored = str(it.get("status") or "")
        new = str(derived[key].get("status") or "")
        if stored != new:
            plan.append({"id": key, "name": it.get("name") or key, "stored": stored,
                         "derived": new, "live": live.get(key, 0)})
    return plan


# ---------------------------------------------------------------------------
# Alerts (server-side port of the bench's alertsFor(), computed in Python)
# ---------------------------------------------------------------------------
def _alert(severity: str, kind: str, detail: str, item_id: Any = None,
           ref_id: Any = None, days: Optional[int] = None) -> Dict[str, Any]:
    """One alert record. itemId/refId/days are additive metadata for the UI
    (deep-link to the item; overdue days from the SERVER clock so the
    Dashboard and the Reports page agree) — the snapshot renders only
    severity/kind/detail, so the LLM-facing text is unchanged."""
    return {
        "severity": severity,
        "kind": kind,
        "detail": detail,
        "itemId": str(item_id) if item_id is not None else None,
        "refId": str(ref_id) if ref_id is not None else None,
        "days": days,
    }


def alerts_for(
    items: Sequence[Dict],
    open_loans: Sequence[Dict],
    reservations: Sequence[Dict],
    plaxis: Sequence[Dict],
    now: datetime,
) -> List[Dict[str, Any]]:
    """Alert records (see _alert). Deterministic; sorted high→low."""
    items = active_items(items)
    items_by_id = {str(i.get("id")): i for i in items}
    alerts: List[Dict[str, Any]] = []

    # Overdue loans.
    for tx in open_loans:
        due = _as_dt(tx.get("expectedReturn"))
        if due is not None and due < now:
            days = (now - due).days
            alerts.append(_alert(
                "high", "overdue",
                f"{tx.get('user') or 'unknown'} has {tx.get('qty') or 1}x "
                f"{_item_name(items_by_id, tx.get('itemId'))} overdue since "
                f"{_fmt_dt(due)} ({days}d)",
                item_id=tx.get("itemId"), ref_id=tx.get("id"), days=days,
            ))

    for it in items:
        name = it.get("name") or it.get("id")
        # Consumables at or below minStock.
        min_stock = it.get("minStock")
        if it.get("kind") == "consumable" and isinstance(min_stock, (int, float)):
            qty = it.get("qty") or 0
            if qty <= min_stock:
                sev = "high" if qty <= 0 else "medium"
                alerts.append(_alert(
                    sev, "low_stock",
                    f"{name}: {qty} {it.get('unit') or 'units'} left (min {min_stock})",
                    item_id=it.get("id"),
                ))
        # Expiry within 45 days.
        expiry = _as_dt(it.get("expiryDate"))
        if expiry is not None and expiry <= now + timedelta(days=45):
            if expiry < now:
                alerts.append(_alert("high", "expired", f"{name} expired {_fmt_dt(expiry)}",
                                     item_id=it.get("id"), days=(now - expiry).days))
            else:
                alerts.append(_alert("medium", "expiry", f"{name} expires {_fmt_dt(expiry)}",
                                     item_id=it.get("id"), days=(expiry - now).days))
        # Maintenance due within 30 days (lastMaint + maintDays).
        maint_days = it.get("maintDays")
        last_maint = _as_dt(it.get("lastMaint"))
        if isinstance(maint_days, (int, float)) and maint_days > 0 and last_maint is not None:
            due = last_maint + timedelta(days=float(maint_days))
            if due <= now:
                alerts.append(_alert("high", "maintenance",
                                     f"{name} maintenance overdue since {_fmt_dt(due)}",
                                     item_id=it.get("id"), days=(now - due).days))
            elif due <= now + timedelta(days=30):
                alerts.append(_alert("medium", "maintenance", f"{name} maintenance due {_fmt_dt(due)}",
                                     item_id=it.get("id"), days=(due - now).days))
        # Damaged / missing.
        flags = {str(it.get("condition") or "").lower(), str(it.get("status") or "").lower()}
        if "damaged" in flags:
            alerts.append(_alert("high", "damaged", f"{name} is marked damaged", item_id=it.get("id")))
        if "missing" in flags:
            alerts.append(_alert("high", "missing", f"{name} is marked missing", item_id=it.get("id")))

    # PLAXIS seats held past end time.
    for s in plaxis:
        if s.get("loggedOut"):
            continue
        end = _as_dt(s.get("end"))
        if end is not None and end < now:
            alerts.append(_alert(
                "high", "plaxis_overrun",
                f"PLAXIS seat {s.get('seat')} held by {s.get('user') or 'unknown'} "
                f"past {_fmt_dt(end)}",
                ref_id=s.get("id"),
            ))

    # Upcoming reservations: APPROVED bookings starting within 48 h (the
    # Dashboard's heads-up; the 24 h email digest is the reminder).
    for r in reservations:
        if str(r.get("status") or "").lower() != "approved":
            continue
        start = _as_dt(r.get("start"))
        if start is None or not (now <= start <= now + timedelta(hours=48)):
            continue
        alerts.append(_alert(
            "low", "upcoming_reservation",
            f"{r.get('user') or 'unknown'}: {_item_name(items_by_id, r.get('itemId'))} "
            f"reserved {_fmt_dt(start)} → {_fmt_dt(r.get('end'))}",
            item_id=r.get("itemId"), ref_id=r.get("id"),
            days=(start - now).days,
        ))

    # Reservations pending approval.
    for r in reservations:
        if str(r.get("status") or "").lower() == "pending":
            alerts.append(_alert(
                "low", "pending_approval",
                f"{r.get('user') or 'unknown'}: {_item_name(items_by_id, r.get('itemId'))} "
                f"{_fmt_dt(r.get('start'))} → {_fmt_dt(r.get('end'))} awaits approval",
                item_id=r.get("itemId"), ref_id=r.get("id"),
            ))

    # Conflicting bookings already in the data (pre-Phase-1 rows, or a loan
    # that overran into a reservation). One alert per overlapping pair.
    seen_pairs: set = set()
    for it in items:
        item_res = [r for r in reservations
                    if str(r.get("itemId")) == str(it.get("id"))
                    and str(r.get("status") or "").lower() != "denied"]
        for r in item_res:
            r_start, r_end = _as_dt(r.get("start")), _as_dt(r.get("end"))
            if r_start is None or r_end is None:
                continue
            holders = reservation_conflicts(
                it, r_start, r_end, int(r.get("qty") or 1), open_loans, reservations,
                exclude_id=r.get("id"),
            )
            for h in holders:
                pair = frozenset({str(r.get("id")), str(h.get("id"))})
                if pair in seen_pairs:
                    continue
                seen_pairs.add(pair)
                alerts.append(_alert(
                    "high", "conflict",
                    f"{it.get('name') or it.get('id')}: {r.get('user') or 'unknown'} "
                    f"({_fmt_dt(r_start)} → {_fmt_dt(r_end)}) overlaps {h['user']} "
                    f"({h['start']} → {h['end']})",
                    item_id=it.get("id"), ref_id=r.get("id"),
                ))

    order = {"high": 0, "medium": 1, "low": 2}
    alerts.sort(key=lambda a: (order.get(a["severity"], 3), a["kind"], a["detail"]))
    return alerts


# ---------------------------------------------------------------------------
# Snapshot serializer (pure core + async shell)
# ---------------------------------------------------------------------------
@dataclass
class SnapshotResult:
    """The rendered snapshot plus the bookkeeping the scope note is built from."""

    text: str
    included: List[str]
    omitted: List[str]            # sections dropped by the token cap
    trimmed: Dict[str, int]       # section -> rows dropped by the token cap
    taken_at: datetime
    token_estimate: int

    def scope_note(self) -> str:
        """Deterministic user-facing note: timestamp + what the snapshot held.

        Assembled from the serializer's own output, never from model prose
        (the _thread_scope_note pattern)."""
        note = (
            f"_Inventory snapshot taken {self.taken_at.strftime('%Y-%m-%d %H:%M')} — "
            f"sections included: {', '.join(self.included) or 'none'}."
        )
        drops = list(self.omitted)
        drops += [f"{sec} ({n} rows)" for sec, n in self.trimmed.items() if n]
        if drops:
            note += f" Omitted for length: {', '.join(drops)}."
        note += "_"
        return note


def _items_section(items: Sequence[Dict]) -> List[str]:
    lines = [ITEMS, "id | name | qty | out | avail | unit | status | condition | location | custodian"]
    for it in sorted(items, key=lambda d: str(d.get("id"))):
        qty = it.get("qty") or 0
        out = it.get("qtyOut") or 0
        lines.append(_row([
            it.get("id"), it.get("name"), qty, out, qty - out, it.get("unit"),
            it.get("status"), it.get("condition"), it.get("location"), it.get("custodian"),
        ]))
    return lines


def _loans_section(open_loans: Sequence[Dict], items_by_id: Dict[str, Dict], now: datetime) -> List[str]:
    lines = [OPEN_LOANS, "item | user | qty | since | due | overdue_days | purpose"]
    for tx in sorted(open_loans, key=lambda d: (_fmt_dt(d.get("ts")), str(d.get("itemId")))):
        due = _as_dt(tx.get("expectedReturn"))
        overdue = (now - due).days if (due is not None and due < now) else 0
        lines.append(_row([
            _item_name(items_by_id, tx.get("itemId")), tx.get("user"), tx.get("qty") or 1,
            _fmt_dt(tx.get("ts")), _fmt_dt(due), overdue or "-", tx.get("purpose"),
        ]))
    return lines


def _reservations_section(reservations: Sequence[Dict], items_by_id: Dict[str, Dict], now: datetime) -> List[str]:
    lines = [f"{RESERVATIONS} (next 30d)", "item | user | start | end | status | purpose"]
    horizon = now + timedelta(days=30)
    rows = [
        r for r in reservations
        if str(r.get("status") or "").lower() != "denied"
        and (_as_dt(r.get("end")) or now) >= now
        and (_as_dt(r.get("start")) or now) <= horizon
    ]
    for r in sorted(rows, key=lambda d: (_fmt_dt(d.get("start")), str(d.get("itemId")))):
        lines.append(_row([
            _item_name(items_by_id, r.get("itemId")), r.get("user"),
            _fmt_dt(r.get("start")), _fmt_dt(r.get("end")), r.get("status"), r.get("purpose"),
        ]))
    return lines


def _plaxis_section(plaxis: Sequence[Dict]) -> List[str]:
    lines = [f"{PLAXIS} (2 concurrent)", "seat | user | start | end | logged_out"]
    active = [s for s in plaxis if not s.get("loggedOut")]
    for s in sorted(active, key=lambda d: (str(d.get("seat")), _fmt_dt(d.get("start")))):
        lines.append(_row([
            s.get("seat"), s.get("user"), _fmt_dt(s.get("start")), _fmt_dt(s.get("end")), "no",
        ]))
    if not active:
        lines.append("(all seats free)")
    return lines


def _alerts_section(alerts: Sequence[Dict[str, Any]]) -> List[str]:
    lines = [ALERTS, "severity | kind | detail"]
    for a in alerts:
        lines.append(_row([a["severity"], a["kind"], a["detail"]]))
    if not alerts:
        lines.append("(none)")
    return lines


def render_snapshot(
    data: Dict[str, List[Dict]],
    scope: str,
    now: datetime,
    cap_tokens: Optional[int] = None,
) -> SnapshotResult:
    """Pure renderer. ``data`` holds prefetched ``items`` / ``open_loans`` /
    ``reservations`` / ``plaxis`` lists; ``scope`` picks sections; the cap
    drops whole sections (SECTION_DROP_ORDER), then whole rows from the
    largest section — never a partial row."""
    cap = cap_tokens if cap_tokens is not None else config.INVENTORY_SNAPSHOT_TOKEN_CAP
    items = active_items(data.get("items") or [])
    open_loans = [t for t in (data.get("open_loans") or []) if _is_open_loan(t)]
    reservations = data.get("reservations") or []
    plaxis = data.get("plaxis") or []
    items_by_id = {str(i.get("id")): i for i in items}

    wanted = _parse_scope(scope)
    builders = {
        ITEMS: lambda: _items_section(items),
        OPEN_LOANS: lambda: _loans_section(open_loans, items_by_id, now),
        RESERVATIONS: lambda: _reservations_section(reservations, items_by_id, now),
        PLAXIS: lambda: _plaxis_section(plaxis),
        ALERTS: lambda: _alerts_section(
            alerts_for(items, open_loans, reservations, plaxis, now)
        ),
    }
    sections: Dict[str, List[str]] = {s: builders[s]() for s in wanted}
    omitted: List[str] = []
    trimmed: Dict[str, int] = {}

    def _render() -> str:
        return "\n\n".join("\n".join(sections[s]) for s in SECTION_ORDER if s in sections)

    # 1) Drop whole sections by priority while over the cap (keep at least one).
    for candidate in SECTION_DROP_ORDER:
        if _est_tokens(_render()) <= cap:
            break
        if candidate in sections and len(sections) > 1:
            del sections[candidate]
            omitted.append(candidate)

    # 2) Still over: drop whole rows from the end of the largest section.
    # Header (2 lines) always survives; a trim marker states the drop count.
    while _est_tokens(_render()) > cap:
        largest = max(sections, key=lambda s: len(sections[s]), default=None)
        if largest is None or len(sections[largest]) <= 3:
            break  # nothing left worth cutting; a tiny overshoot beats an empty table
        body = sections[largest]
        if body[-1].startswith("(+"):
            body.pop(-2)
        else:
            body.pop()
        trimmed[largest] = trimmed.get(largest, 0) + 1
        marker = f"(+{trimmed[largest]} more rows omitted)"
        if body[-1].startswith("(+"):
            body[-1] = marker
        else:
            body.append(marker)

    text = _render()
    return SnapshotResult(
        text=text,
        included=[s for s in SECTION_ORDER if s in sections],
        omitted=omitted,
        trimmed=trimmed,
        taken_at=now,
        token_estimate=_est_tokens(text),
    )


async def _fetch_inventory_data() -> Dict[str, List[Dict]]:
    """One read per collection, _id excluded (the snapshot and feasibility
    walk key on the domain ``id`` fields)."""
    from app.core.database import (
        inv_items_collection,
        inv_plaxis_collection,
        inv_res_collection,
        inv_tx_collection,
    )

    items = [d async for d in inv_items_collection.find({}, {"_id": 0})]
    # Equality-to-null matches BOTH a null field and an absent one, so loans
    # written without the key (the sparse-index shape) are still found.
    open_loans = [
        d async for d in inv_tx_collection.find(
            {"type": "checkout", "actualReturn": None}, {"_id": 0}
        )
    ]
    reservations = [d async for d in inv_res_collection.find({}, {"_id": 0})]
    plaxis = [
        d async for d in inv_plaxis_collection.find(
            {"loggedOut": {"$in": [None, False]}}, {"_id": 0}
        )
    ]
    return {
        # Reserved is derived from the reservation rows on every read, so the
        # snapshot, alerts and feasibility never see a stale stored value.
        "items": derive_reserved(items, reservations, datetime.now()),
        "open_loans": open_loans,
        "reservations": reservations,
        "plaxis": plaxis,
    }


async def build_inventory_snapshot_result(scope: str = "all") -> SnapshotResult:
    data = await _fetch_inventory_data()
    return render_snapshot(data, scope, now=datetime.now())


async def build_inventory_snapshot(scope: str) -> str:
    """The spec'd public entry point: compact pipe-delimited tables for the
    sections ``scope`` needs. Deterministic, zero LLM calls."""
    return (await build_inventory_snapshot_result(scope)).text


# ---------------------------------------------------------------------------
# Feasibility (pure core + async shell). The part the model must not compute.
# ---------------------------------------------------------------------------
@dataclass
class ItemRequest:
    itemId: str
    qty: int = 1


@dataclass
class ItemFeasibility:
    itemId: str
    name: str
    requested: int
    status: str                     # available | short_by | conflicts_with | unknown_item
    short_by: int = 0
    conflicts: List[Dict[str, str]] = field(default_factory=list)  # user/start/end


@dataclass
class FeasibilityReport:
    start: datetime
    end: datetime
    items: List[ItemFeasibility]
    feasible: bool
    earliest_available: Optional[datetime]


def _overlaps(res: Dict, start: datetime, end: datetime) -> bool:
    """Half-open overlap: ``res.start < end and res.end > start`` — a
    reservation ending exactly at ``start`` (or starting exactly at ``end``)
    does NOT conflict."""
    r_start, r_end = _as_dt(res.get("start")), _as_dt(res.get("end"))
    if r_start is None or r_end is None:
        return False
    return r_start < end and r_end > start


def _loan_overlaps(tx: Dict, start: datetime, end: datetime) -> bool:
    """An open loan occupies [ts, expectedReturn) — open-ended when it has no
    expected return (the gear is out until it comes back). Half-open, like
    reservations."""
    l_start = _as_dt(tx.get("ts")) or datetime.min
    l_end = _as_dt(tx.get("expectedReturn"))
    if l_end is None:
        return l_start < end
    return l_start < end and l_end > start


def reservation_conflicts(
    item: Dict,
    start: datetime,
    end: datetime,
    qty: int,
    open_loans: Sequence[Dict],
    reservations: Sequence[Dict],
    exclude_id: Any = None,
) -> List[Dict[str, Any]]:
    """The overlap gate (Phase 1). Quantity-aware: committed units in
    [start, end) — open loans (equipment only; consumables are consumed at
    checkout, never on loan) plus every NON-denied reservation, half-open
    overlap ``a.start < b.end and a.end > b.start`` — plus the requested
    ``qty`` must not exceed the item's ``qty``. Returns the holders that
    share the window when it would (empty list == allowed). ``exclude_id``
    skips the reservation being edited/approved so it never conflicts with
    itself."""
    item_id = str(item.get("id"))
    capacity = int(item.get("qty") or 0)
    requested = max(int(qty or 1), 0)
    holders: List[Dict[str, Any]] = []
    committed = 0
    if item.get("kind") != "consumable":
        for tx in open_loans:
            if not _is_open_loan(tx) or str(tx.get("itemId")) != item_id:
                continue
            if _loan_overlaps(tx, start, end):
                committed += int(tx.get("qty") or 1)
                holders.append({
                    "kind": "loan", "id": tx.get("id"), "user": tx.get("user") or "unknown",
                    "qty": int(tx.get("qty") or 1), "start": _fmt_dt(tx.get("ts")),
                    "end": _fmt_dt(tx.get("expectedReturn")) or "open",
                })
    for r in reservations:
        if str(r.get("itemId")) != item_id:
            continue
        if exclude_id is not None and str(r.get("id")) == str(exclude_id):
            continue
        if str(r.get("status") or "").lower() == "denied":
            continue
        if _overlaps(r, start, end):
            committed += int(r.get("qty") or 1)
            holders.append({
                "kind": "reservation", "id": r.get("id"), "user": r.get("user") or "unknown",
                "qty": int(r.get("qty") or 1), "start": _fmt_dt(r.get("start")),
                "end": _fmt_dt(r.get("end")), "status": r.get("status") or "Pending",
            })
    if committed + requested <= capacity:
        return []
    return holders


def conflict_message(item: Dict, holders: Sequence[Dict[str, Any]]) -> str:
    """The 409 body: names every holder and window sharing the requested slot."""
    parts = [f"{h['user']} ({h['start']} → {h['end']})" for h in holders]
    return (f"{item.get('name') or item.get('id')} is already committed for that window — "
            f"conflicts with {'; '.join(parts) if parts else 'existing bookings'}.")


# The lab license: two concurrent seats, numbered 0 and 1 ("Seat 1"/"Seat 2"
# in the UI). The seat-window gate below is the server-side counterpart of
# the client's presentation-only seatConflicts pre-check.
PLAXIS_SEATS = (0, 1)


def seat_conflicts(
    sessions: Sequence[Dict],
    seat: Any,
    start: datetime,
    end: datetime,
    exclude_id: Any = None,
) -> List[Dict[str, Any]]:
    """HELD sessions (``loggedOut`` falsy) on ``seat`` overlapping the
    half-open window ``a.start < b.end and a.end > b.start`` — back-to-back
    bookings never conflict, exactly like reservation_conflicts. NOT
    owner-aware: every held session counts regardless of who holds it (a
    seat is physical state). ``exclude_id`` skips the row being edited so it
    never conflicts with itself. Holder dicts feed conflict_message
    unchanged."""
    holders: List[Dict[str, Any]] = []
    for s in sessions:
        if s.get("loggedOut"):
            continue
        if s.get("seat") != seat:
            continue
        if exclude_id is not None and str(s.get("id")) == str(exclude_id):
            continue
        s_start, s_end = _as_dt(s.get("start")), _as_dt(s.get("end"))
        if s_start is None or s_end is None:
            continue
        if s_start < end and s_end > start:
            holders.append({
                "kind": "plaxis", "id": s.get("id"), "user": s.get("user") or "unknown",
                "start": _fmt_dt(s_start), "end": _fmt_dt(s_end),
            })
    return holders


def _availability(
    item: Dict,
    req_qty: int,
    start: datetime,
    end: datetime,
    open_loans: Sequence[Dict],
    approved_res: Sequence[Dict],
    loan_frees_at: Optional[Dict[int, Optional[datetime]]] = None,
) -> Tuple[int, List[Dict]]:
    """(available_qty, overlapping approved reservations) for one item.

    ``loan_frees_at`` is the projection hook for the earliest-availability
    scan: index -> when that open loan is assumed back (its expectedReturn).
    When absent (the real check), EVERY open loan counts — the gear is
    physically out regardless of the window."""
    item_id = str(item.get("id"))
    qty = item.get("qty") or 0
    if item.get("kind") == "consumable":
        # Consumption model: a consumable checkout decrements qty at
        # transaction time and never returns, so on-hand qty IS availability
        # (minus any approved reservation overlapping the window).
        overlapping = [r for r in approved_res if str(r.get("itemId")) == item_id and _overlaps(r, start, end)]
        reserved = sum(int(r.get("qty") or 1) for r in overlapping)
        return qty - reserved, overlapping

    out = 0
    for idx, tx in enumerate(open_loans):
        if str(tx.get("itemId")) != item_id:
            continue
        if loan_frees_at is not None:
            frees = loan_frees_at.get(idx)
            if frees is not None and frees <= start:
                continue  # projected back before the window opens
        out += int(tx.get("qty") or 1)
    overlapping = [r for r in approved_res if str(r.get("itemId")) == item_id and _overlaps(r, start, end)]
    reserved = sum(int(r.get("qty") or 1) for r in overlapping)
    return qty - out - reserved, overlapping


def compute_feasibility(
    requests: Sequence[ItemRequest],
    start: datetime,
    end: datetime,
    items: Sequence[Dict],
    open_loans: Sequence[Dict],
    approved_res: Sequence[Dict],
    now: Optional[datetime] = None,
) -> FeasibilityReport:
    """Pure feasibility walk. Per request: available | short_by(n) |
    conflicts_with(user, dates); plus the overall verdict and the earliest
    date the FULL set becomes available (scanning loan expectedReturns and
    reservation ends as the only times availability can change)."""
    now = now or datetime.now()
    items_by_id = {str(i.get("id")): i for i in active_items(items)}
    open_loans = [t for t in open_loans if _is_open_loan(t)]
    approved_res = [r for r in approved_res if str(r.get("status") or "").lower() == "approved"]

    results: List[ItemFeasibility] = []
    for req in requests:
        item = items_by_id.get(str(req.itemId))
        if item is None:
            results.append(ItemFeasibility(
                itemId=str(req.itemId), name=str(req.itemId), requested=req.qty,
                status="unknown_item",
            ))
            continue
        name = item.get("name") or str(req.itemId)
        if req.qty <= 0:
            # Zero-qty request: nothing is being asked for — trivially available.
            results.append(ItemFeasibility(
                itemId=str(req.itemId), name=name, requested=0, status="available",
            ))
            continue
        avail, overlapping = _availability(item, req.qty, start, end, open_loans, approved_res)
        if avail >= req.qty:
            results.append(ItemFeasibility(
                itemId=str(req.itemId), name=name, requested=req.qty, status="available",
            ))
        else:
            conflicts = [
                {"user": str(r.get("user") or "unknown"),
                 "start": _fmt_dt(r.get("start")), "end": _fmt_dt(r.get("end"))}
                for r in overlapping
            ]
            results.append(ItemFeasibility(
                itemId=str(req.itemId), name=name, requested=req.qty,
                status="conflicts_with" if conflicts else "short_by",
                short_by=req.qty - max(avail, 0),
                conflicts=conflicts,
            ))

    feasible = all(r.status == "available" for r in results)
    earliest = start if feasible else _earliest_full_availability(
        requests, start, end, items_by_id, open_loans, approved_res
    )
    return FeasibilityReport(
        start=start, end=end, items=results, feasible=feasible,
        earliest_available=earliest,
    )


def _earliest_full_availability(
    requests: Sequence[ItemRequest],
    start: datetime,
    end: datetime,
    items_by_id: Dict[str, Dict],
    open_loans: Sequence[Dict],
    approved_res: Sequence[Dict],
) -> Optional[datetime]:
    """Earliest window start >= ``start`` (same duration) satisfying every
    request. Availability only changes when a loan is expected back or a
    reservation ends, so ONLY those instants are candidates. Loans without an
    expectedReturn never free in projection; a shortage that no candidate
    cures returns None (not schedulable from current data)."""
    duration = end - start
    wanted_ids = {str(r.itemId) for r in requests}
    loan_frees_at: Dict[int, Optional[datetime]] = {
        idx: _as_dt(tx.get("expectedReturn")) for idx, tx in enumerate(open_loans)
    }
    candidates = {start}
    for idx, tx in enumerate(open_loans):
        frees = loan_frees_at.get(idx)
        if str(tx.get("itemId")) in wanted_ids and frees is not None and frees > start:
            candidates.add(frees)
    for r in approved_res:
        r_end = _as_dt(r.get("end"))
        if str(r.get("itemId")) in wanted_ids and r_end is not None and r_end > start:
            candidates.add(r_end)

    for t in sorted(candidates):
        ok = True
        for req in requests:
            if req.qty <= 0:
                continue
            item = items_by_id.get(str(req.itemId))
            if item is None:
                return None  # an unknown item can never become available
            avail, _ = _availability(
                item, req.qty, t, t + duration, open_loans, approved_res,
                loan_frees_at=loan_frees_at,
            )
            if avail < req.qty:
                ok = False
                break
        if ok:
            return t
    return None


async def check_feasibility(
    requests: List[ItemRequest],
    start: datetime,
    end: datetime,
) -> FeasibilityReport:
    """Async shell: fetch items + open loans + approved reservations for the
    requested items, then run the pure walk."""
    from app.core.database import (
        inv_items_collection,
        inv_res_collection,
        inv_tx_collection,
    )

    ids = sorted({str(r.itemId) for r in requests})
    items = [d async for d in inv_items_collection.find({"id": {"$in": ids}}, {"_id": 0})]
    open_loans = [
        d async for d in inv_tx_collection.find(
            {"type": "checkout", "actualReturn": None, "itemId": {"$in": ids}}, {"_id": 0}
        )
    ]
    approved_res = [
        d async for d in inv_res_collection.find(
            {"itemId": {"$in": ids}, "status": "Approved"}, {"_id": 0}
        )
    ]
    return compute_feasibility(requests, start, end, items, open_loans, approved_res)


def render_feasibility_report(report: FeasibilityReport) -> str:
    """Deterministic pipe-table block the answer LLM narrates verbatim facts
    from. Never assembled from model prose."""
    lines = [
        f"FEASIBILITY CHECK ({_fmt_dt(report.start)} → {_fmt_dt(report.end)})",
        "item | requested | status | detail",
    ]
    for r in report.items:
        if r.status == "available":
            detail = "-"
        elif r.status == "unknown_item":
            detail = "no such item in the inventory"
        else:
            parts = []
            if r.short_by:
                parts.append(f"short by {r.short_by}")
            parts += [f"conflicts with {c['user']} ({c['start']} → {c['end']})" for c in r.conflicts]
            detail = "; ".join(parts) or "-"
        lines.append(_row([r.name, r.requested, r.status, detail]))
    lines.append(f"VERDICT: {'FEASIBLE' if report.feasible else 'NOT FEASIBLE'}")
    lines.append(
        "EARLIEST FULL AVAILABILITY: "
        + (_fmt_dt(report.earliest_available) if report.earliest_available else "not schedulable from current data")
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# NL request extraction (the ONLY LLM step: parse, never calculate)
# ---------------------------------------------------------------------------
_EXTRACT_SYSTEM_PROMPT = (
    "You translate a lab member's message into an equipment-booking check. "
    "Read the LATEST MESSAGE and the ITEM CATALOG, and return ONLY JSON of "
    'the form {"requests": [{"itemId": "<id>", "qty": <int>}], '
    '"start": "YYYY-MM-DDTHH:MM", "end": "YYYY-MM-DDTHH:MM"}.\n'
    "Rules:\n"
    "1. Use ONLY itemId values from the catalog. Match by name; when no "
    "catalog item plausibly matches, leave it out.\n"
    '2. If the message does NOT ask whether items can be used/booked/borrowed '
    'for a time window, return {"requests": []}.\n'
    "3. Resolve relative dates against TODAY. A date without a time means "
    "00:00 for start and 23:59 for end. qty defaults to 1.\n"
    "4. No prose, no explanation, no extra keys."
)


def _strip_fences(raw: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.DOTALL | re.IGNORECASE).strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, re.DOTALL)
    return match.group(0) if match else text


def parse_feasibility_extraction(
    raw: str, valid_ids: Sequence[str]
) -> Optional[Tuple[List[ItemRequest], datetime, datetime]]:
    """Defensive parse of the extractor output (same posture as _parse_mode):
    any malformed JSON, unknown itemId, bad date, or empty/absent requests
    returns None — the turn then answers from the snapshot alone."""
    try:
        data = json.loads(_strip_fences(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    reqs_raw = data.get("requests")
    if not isinstance(reqs_raw, list) or not reqs_raw:
        return None
    valid = {str(v) for v in valid_ids}
    requests: List[ItemRequest] = []
    for entry in reqs_raw:
        if not isinstance(entry, dict):
            return None
        item_id = str(entry.get("itemId") or "").strip()
        if item_id not in valid:
            return None
        try:
            qty = int(entry.get("qty", 1))
        except (TypeError, ValueError):
            return None
        requests.append(ItemRequest(itemId=item_id, qty=qty))
    start = _as_dt(data.get("start"))
    end = _as_dt(data.get("end"))
    if start is None or end is None or end <= start:
        return None
    return requests, start, end


async def extract_feasibility_request(
    message: str,
    items: Sequence[Dict],
    client: Optional[Any] = None,
) -> Optional[Tuple[List[ItemRequest], datetime, datetime]]:
    """Ask the router-sized LLM to parse the message into ItemRequests + a
    window. Never raises: any failure returns None and the caller answers
    from the snapshot alone. The LLM only PARSES — check_feasibility does the
    arithmetic."""
    import ollama

    if client is None:
        client = ollama.AsyncClient(
            host=config.OLLAMA_BASE_URL, timeout=config.OLLAMA_REQUEST_TIMEOUT
        )
    catalog = "\n".join(
        f"{i.get('id')}: {i.get('name')} ({i.get('kind') or 'equipment'})"
        for i in sorted(items, key=lambda d: str(d.get("id")))[:100]
    )
    user_prompt = (
        f"TODAY: {datetime.now().strftime('%Y-%m-%d')}\n\n"
        f"ITEM CATALOG:\n{catalog or '(empty)'}\n\n"
        f"LATEST MESSAGE: {(message or '').strip()}\n\n"
        "Respond with ONLY the JSON object."
    )
    try:
        resp = await client.chat(
            model=config.OLLAMA_MODEL,
            messages=[
                {"role": "system", "content": _EXTRACT_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            think=False,
            format="json",
            options={"num_ctx": config.OLLAMA_NUM_CTX, "num_predict": 256, "temperature": 0.0},
        )
    except Exception as exc:  # noqa: BLE001 - unreachable model -> snapshot-only answer
        logger.warning("inventory extraction FAILED (%s): %r", exc, message)
        return None
    raw = (resp["message"]["content"] or "") if resp else ""
    parsed = parse_feasibility_extraction(raw, [str(i.get("id")) for i in items])
    logger.info("inventory extraction: %r -> %s", message, "ok" if parsed else "none")
    return parsed


# ---------------------------------------------------------------------------
# Chat-turn assembly: one fetch feeds snapshot + feasibility
# ---------------------------------------------------------------------------
async def prepare_inventory_context(
    message: str, client: Optional[Any] = None
) -> Tuple[str, str]:
    """Everything an INVENTORY chat turn needs: ``(context, scope_note)``.

    One Mongo fetch feeds both the snapshot (sections inferred from the
    message) and — when the extractor parses a booking request out of the
    message — the feasibility engine, whose rendered report is appended to
    the context. The note is deterministic serializer output; the extraction
    is the turn's ONLY inventory LLM call and any failure there degrades to a
    snapshot-only answer.
    """
    data = await _fetch_inventory_data()
    result = render_snapshot(data, infer_inventory_scope(message), now=datetime.now())
    context = result.text
    extraction = await extract_feasibility_request(message, data["items"], client=client)
    if extraction is not None:
        requests, start, end = extraction
        report = compute_feasibility(
            requests, start, end,
            data["items"], data["open_loans"], data["reservations"],
        )
        context = f"{context}\n\n{render_feasibility_report(report)}"
    return context, result.scope_note()
