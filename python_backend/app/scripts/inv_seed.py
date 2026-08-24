"""One-shot idempotent seeder for the lab inventory collections.

Ports the Lin Lab bench seed data VERBATIM from
``python_backend/data/linlab_seed.json`` (supplied by the owner 2026-08-21;
it already carries the ODiSI **6104** correction). The file holds two
variants, chosen by flag:

  default   : ``reference_only_clean_ledger`` — the real users + items with a
              clean ledger (no activity). The honest starting state for the
              live system; loans/reservations then accrue through the app.
  --demo    : ``reference_with_demo_activity`` — the same users + items plus
              the bench's DEMO transactions / reservations / PLAXIS sessions /
              audit rows (the file's own _note labels these as demo activity).
              Item qty/qtyOut in this variant already reflect the demo
              checkouts, so rows are inserted verbatim with NO side effects.

NO-OPS when inv_items is non-empty, so re-running can never duplicate or
overwrite live data. ``--dry-run`` parses, coerces and validates without
importing the database layer or writing anything.

Run (from the python_backend directory):
    python -m app.scripts.inv_seed --dry-run          # validate only
    python -m app.scripts.inv_seed                    # seed clean ledger
    python -m app.scripts.inv_seed --demo             # seed with demo activity
"""
from __future__ import annotations

import argparse
import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

DATA_PATH = Path(__file__).resolve().parents[2] / "data" / "linlab_seed.json"

VARIANTS = {
    False: "reference_only_clean_ledger",
    True: "reference_with_demo_activity",
}

# ISO-date fields coerced to naive datetimes at seed time so the snapshot /
# feasibility date math never sees strings. users.since is deliberately NOT
# here: it is a year label ("2024"), not an ISO date.
_DATE_FIELDS = frozenset({
    "purchaseDate", "expiryDate", "lastMaint", "ts", "expectedReturn",
    "actualReturn", "start", "end",
})

# JSON section -> inv_* collection attribute name in app.core.database.
_COLLECTION_ATTRS = {
    "users": "inv_users_collection",
    "items": "inv_items_collection",
    "tx": "inv_tx_collection",
    "res": "inv_res_collection",
    "plaxis": "inv_plaxis_collection",
    "audit": "inv_audit_collection",
}


def _coerce_dates(doc: dict) -> dict:
    """ISO strings -> naive datetimes (Z treated as UTC); "" -> None."""
    out = dict(doc)
    for key in _DATE_FIELDS & out.keys():
        value = out[key]
        if isinstance(value, str):
            value = value.strip()
            if not value:
                out[key] = None
            else:
                out[key] = datetime.fromisoformat(
                    value.replace("Z", "+00:00")
                ).replace(tzinfo=None)
    return out


def load_seed(demo: bool) -> Dict[str, List[dict]]:
    """Load + coerce the chosen variant. Raises on unparseable data — a bad
    seed file must fail loudly, never half-seed."""
    raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
    variant = raw[VARIANTS[demo]]
    return {
        section: [_coerce_dates(d) for d in variant.get(section, [])]
        for section in _COLLECTION_ATTRS
        if section in variant
    }


def validate_seed(seed: Dict[str, List[dict]]) -> List[str]:
    """Deterministic sanity checks; returns problem strings (empty == clean)."""
    problems: List[str] = []
    items = {str(d.get("id")): d for d in seed.get("items", [])}
    if not items:
        problems.append("no items in seed")
    for section in ("tx", "res"):
        for d in seed.get(section, []):
            if str(d.get("itemId")) not in items:
                problems.append(f"{section} {d.get('id')}: unknown itemId {d.get('itemId')!r}")
    for section, docs in seed.items():
        ids = [str(d.get("id")) for d in docs]
        if len(ids) != len(set(ids)):
            problems.append(f"{section}: duplicate ids")
    for d in items.values():
        if "6100" in str(d.get("model", "")):
            problems.append(f"item {d['id']}: interrogator model must read ODiSI 6104, not 6100")
        if (d.get("qtyOut") or 0) > (d.get("qty") or 0):
            problems.append(f"item {d['id']}: qtyOut {d.get('qtyOut')} exceeds qty {d.get('qty')}")
    return problems


def _summarize(seed: Dict[str, List[dict]]) -> str:
    return ", ".join(f"{len(docs)} {name}" for name, docs in seed.items())


async def seed_db(seed: Dict[str, List[dict]]) -> None:
    from app.core import database

    existing = await database.inv_items_collection.count_documents({})
    if existing > 0:
        print(f"[INV_SEED] inv_items already holds {existing} items -- nothing to do.")
        return
    now = datetime.now()
    for section, docs in seed.items():
        if not docs:
            continue
        coll = getattr(database, _COLLECTION_ATTRS[section])
        stamped = [{**d, "createdAt": now, "updatedAt": now} for d in docs]
        await coll.insert_many(stamped)
        print(f"[INV_SEED] inserted {len(stamped)} docs into inv_{section}")
    print("[INV_SEED] done.")


async def _main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--demo", action="store_true",
                        help="seed the demo-activity variant instead of the clean ledger")
    parser.add_argument("--dry-run", action="store_true",
                        help="parse + validate only; no database access")
    args = parser.parse_args()

    seed = load_seed(demo=args.demo)
    print(f"[INV_SEED] variant: {VARIANTS[args.demo]} ({_summarize(seed)})")
    problems = validate_seed(seed)
    for p in problems:
        print(f"[INV_SEED] PROBLEM: {p}")
    if problems:
        raise SystemExit(f"[INV_SEED] {len(problems)} problem(s) -- refusing to seed.")
    if args.dry_run:
        print("[INV_SEED] dry run: validation clean, nothing written.")
        return

    from app.core.database import close_mongo_connection

    try:
        await seed_db(seed)
    finally:
        await close_mongo_connection()


if __name__ == "__main__":
    asyncio.run(_main())
