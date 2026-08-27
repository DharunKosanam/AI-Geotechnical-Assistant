"""Inventory reminders (INVENTORY_REMINDERS_ENABLED): one daily digest email
per lab member covering reservations starting within 24 h and loans due
within 24 h or already overdue.

Pure core (``build_digests``, ``render_digest``) with an explicit ``now`` for
tests; ``run_reminders`` is the async shell. Idempotent: each send is
recorded in inv_reminders keyed (email, day) and never repeated that day, so
a restart cannot re-send. Delivery goes through the existing email_service
provider abstraction (console / resend / brevo) — tests inject a fake sender
and never touch a real provider.

Scheduling: the backend runs one uvicorn worker (Step 0), so an in-process
hourly ticker (``reminders_loop``) is safe and cannot double-fire; the same
run is also exposed as a manager-only endpoint (POST /api/inventory/
reminders/run) for cron or manual use.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence

from app.core import config
from app.services.inventory_service import _as_dt, _fmt_dt, _is_open_loan, active_items

logger = logging.getLogger(__name__)

WINDOW = timedelta(hours=24)


@dataclass
class Digest:
    email: str
    name: str
    upcoming: List[Dict[str, Any]] = field(default_factory=list)   # reservations starting <= 24 h
    due_soon: List[Dict[str, Any]] = field(default_factory=list)   # loans due <= 24 h
    overdue: List[Dict[str, Any]] = field(default_factory=list)    # loans past due

    @property
    def empty(self) -> bool:
        return not (self.upcoming or self.due_soon or self.overdue)


def _roster_email(users: Sequence[Dict], name: str) -> str:
    n = (name or "").strip().lower()
    for u in users:
        if (u.get("name") or "").strip().lower() == n and u.get("email"):
            return str(u["email"]).strip()
    return ""


def build_digests(
    items: Sequence[Dict],
    open_loans: Sequence[Dict],
    reservations: Sequence[Dict],
    users: Sequence[Dict],
    now: datetime,
) -> Dict[str, Digest]:
    """email -> Digest. Reservations resolve their recipient through the
    roster by name (inv_res has no email); loans use the recorded borrower
    email, falling back to the roster. People with no resolvable email are
    skipped (counted by the caller)."""
    names = {str(i.get("id")): (i.get("name") or i.get("id")) for i in active_items(items)}
    out: Dict[str, Digest] = {}

    def digest_for(email: str, name: str) -> Optional[Digest]:
        e = (email or "").strip().lower()
        if not e:
            return None
        if e not in out:
            out[e] = Digest(email=e, name=name or e)
        return out[e]

    horizon = now + WINDOW
    for r in reservations:
        if str(r.get("status") or "").lower() != "approved":
            continue
        start = _as_dt(r.get("start"))
        if start is None or not (now <= start < horizon):
            continue
        d = digest_for(_roster_email(users, r.get("user") or ""), r.get("user") or "")
        if d is None:
            continue
        d.upcoming.append({
            "item": names.get(str(r.get("itemId")), str(r.get("itemId"))),
            "start": _fmt_dt(start), "end": _fmt_dt(r.get("end")),
            "purpose": r.get("purpose") or "",
        })
    for tx in open_loans:
        if not _is_open_loan(tx):
            continue
        due = _as_dt(tx.get("expectedReturn"))
        if due is None:
            continue
        email = (tx.get("email") or "").strip() or _roster_email(users, tx.get("user") or "")
        d = digest_for(email, tx.get("user") or "")
        if d is None:
            continue
        entry = {
            "item": names.get(str(tx.get("itemId")), str(tx.get("itemId"))),
            "qty": int(tx.get("qty") or 1), "due": _fmt_dt(due),
            "days": (now - due).days if due < now else 0,
        }
        if due < now:
            d.overdue.append(entry)
        elif due < horizon:
            d.due_soon.append(entry)
    return {e: d for e, d in out.items() if not d.empty}


def render_digest(d: Digest, now: datetime) -> tuple:
    """(subject, html, text) — plain, deterministic; nothing model-generated."""
    parts_txt: List[str] = []
    parts_html: List[str] = []

    def section(title: str, rows: List[str]) -> None:
        if not rows:
            return
        parts_txt.append(title + "\n" + "\n".join(f"  - {r}" for r in rows))
        parts_html.append(f"<h3>{title}</h3><ul>" + "".join(f"<li>{r}</li>" for r in rows) + "</ul>")

    section("Overdue — please return", [f"{x['item']} ×{x['qty']}, due {x['due']} ({x['days']} d overdue)" for x in d.overdue])
    section("Due within 24 hours", [f"{x['item']} ×{x['qty']}, due {x['due']}" for x in d.due_soon])
    section("Reservations starting within 24 hours",
            [f"{x['item']}: {x['start']} → {x['end']}" + (f" — {x['purpose']}" if x["purpose"] else "") for x in d.upcoming])
    count = len(d.overdue) + len(d.due_soon) + len(d.upcoming)
    subject = f"Lin Lab inventory: {count} reminder{'s' if count != 1 else ''} for {now.strftime('%Y-%m-%d')}"
    greeting = f"Hi {d.name},"
    text = greeting + "\n\n" + "\n\n".join(parts_txt) + "\n\nThis is the daily inventory digest from the Lin Lab assistant."
    html = (f"<p>{greeting}</p>" + "".join(parts_html)
            + "<p style=\"color:#888\">This is the daily inventory digest from the Lin Lab assistant.</p>")
    return subject, html, text


async def run_reminders(
    now: Optional[datetime] = None,
    sender: Any = None,
    dry_run: bool = False,
    data: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build + send today's digests. Idempotent per (email, day). ``data``
    (items/open_loans/reservations/users) and ``sender`` are injectable for
    tests; in production they come from Mongo and email_service."""
    from app.core.database import inv_reminders_collection, inv_users_collection
    from app.services.inventory_service import _fetch_inventory_data

    now = now or datetime.now()
    day = now.strftime("%Y-%m-%d")
    if data is None:
        data = await _fetch_inventory_data()
        data["users"] = [u async for u in inv_users_collection.find({}, {"_id": 0})]
    digests = build_digests(data["items"], data["open_loans"], data["reservations"], data.get("users", []), now)

    sent, skipped, failed = [], [], []
    for email, d in sorted(digests.items()):
        if not dry_run:
            already = await inv_reminders_collection.find_one({"email": email, "day": day})
            if already:
                skipped.append(email)
                continue
        subject, html, text = render_digest(d, now)
        if dry_run:
            sent.append(email)
            continue
        if sender is None:
            from app.services.email_service import get_email_sender
            sender = get_email_sender()
        ok = False
        try:
            ok = bool(sender.send_email(email, subject, html, text))
        except Exception as exc:  # noqa: BLE001 - one bad address must not stop the run
            logger.warning("reminder send failed for %s: %s", email, exc)
        if not ok:
            failed.append(email)
            continue
        await inv_reminders_collection.insert_one({
            "email": email, "day": day, "sentAt": now,
            "counts": {"overdue": len(d.overdue), "dueSoon": len(d.due_soon), "upcoming": len(d.upcoming)},
        })
        sent.append(email)
    return {"day": day, "dryRun": dry_run, "sent": sent, "skipped": skipped, "failed": failed,
            "candidates": len(digests)}


async def reminders_loop() -> None:
    """In-process daily ticker (single worker — see Step 0). Checks hourly;
    once the local hour reaches INVENTORY_REMINDER_HOUR the idempotent run
    fires (a second tick the same day sends nothing)."""
    while True:
        try:
            now = datetime.now()
            if now.hour >= config.INVENTORY_REMINDER_HOUR:
                summary = await run_reminders(now)
                if summary["sent"]:
                    logger.info("inventory reminders sent: %s", summary["sent"])
        except Exception as exc:  # noqa: BLE001 - keep ticking
            logger.warning("inventory reminders tick failed: %s", exc)
        await asyncio.sleep(3600)
