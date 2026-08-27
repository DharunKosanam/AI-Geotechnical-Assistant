"use client";

/**
 * Inventory modal forms. Each validates before submit and hands a clean
 * payload to the actions layer — no fetch calls in here. Prefill comes from
 * the JWT session user; name/email stay editable for on-behalf transactions.
 *
 * studentId/group on TRANSACTIONS are resolved server-side from the roster
 * (never collected here — the checkout modal only shows what will be
 * recorded). The V-number pattern survives in exactly ONE place: the roster
 * modal below, because inv_users is now the single point where a student id
 * enters the system, so that is where the format gate belongs.
 */
import React, { useMemo, useState } from "react";

import s from "../inventory.module.css";
import { toast } from "../../components/toaster";
import {
  ExportPayload,
  InvItem,
  InvPlaxis,
  InvRes,
  InvTx,
  InvUser,
  SessionPrefill,
  V_NUMBER_RE,
  conflictLine,
  downloadCSV,
  downloadXlsx,
  fmtDate,
  nextMaint,
  reservationConflicts,
  rosterIdentityLine,
  seatConflicts,
  validateImageFile,
} from "../lib";
import { CheckoutForm } from "../actions";
import { Field, Modal } from "./ui";

const CONDITIONS = ["New", "Good", "Fair", "Needs calibration", "Damaged"];
// "Reserved" is deliberately absent: the server derives it from the live
// reservation rows on every read and never stores it (a stored value went
// stale once — LL-SEN-004 read Reserved with no reservation behind it).
const STATUSES = [
  "Available", "In use", "Borrowed", "Under maintenance",
  "Missing", "Depleted", "Retired",
];
const KINDS = ["equipment", "consumable", "software"];

function useField(initial = "") {
  const [value, setValue] = useState(initial);
  return { value, setValue };
}

/** In-flight guard (control audit finding: no create path had one): the
 * submitting control disables on first fire, so a double-click can never
 * produce two rows. Submission closes the modal, so unmount is the
 * re-enable; a validation failure never arms it. */
function useSubmitOnce(): [boolean, (fire: () => void) => void] {
  const [busy, setBusy] = useState(false);
  return [
    busy,
    (fire) => {
      if (busy) return;
      setBusy(true);
      fire();
    },
  ];
}

function validStudentId(v: string): boolean {
  return !v.trim() || V_NUMBER_RE.test(v.trim());
}

// ---------------------------------------------------------------------------
export function CheckoutModal({
  item,
  prefill,
  roster,
  onSubmit,
  onClose,
}: {
  item: InvItem;
  prefill: SessionPrefill;
  roster: InvUser[];
  onSubmit: (form: CheckoutForm) => void;
  onClose: () => void;
}) {
  const avail =
    item.kind === "consumable" ? item.qty ?? 0 : (item.qty ?? 0) - (item.qtyOut ?? 0);
  const [form, setForm] = useState<CheckoutForm>({
    user: prefill.name,
    email: prefill.email,
    qty: 1,
    expectedReturn: "",
    purpose: "",
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, fireOnce] = useSubmitOnce();
  const set = (k: keyof CheckoutForm, v: string | number) =>
    setForm((f) => ({ ...f, [k]: v }));

  const submit = () => {
    const e: Record<string, string> = {};
    if (!form.user.trim()) e.user = "Who is taking it?";
    if (!Number.isInteger(form.qty) || form.qty < 1) e.qty = "At least 1.";
    else if (form.qty > avail) e.qty = `Only ${avail} available.`;
    if (!form.expectedReturn) e.expectedReturn = "A return date is required.";
    setErrors(e);
    if (Object.keys(e).length === 0) fireOnce(() => onSubmit(form));
  };

  // studentId/group are resolved SERVER-side from the roster via the
  // BORROWER's email (the form field, so on-behalf checkouts record the
  // actual borrower) — shown read-only here, live against the typed email,
  // so the user can see what will be recorded.
  const rosterLine = rosterIdentityLine(form.email, roster);

  return (
    <Modal
      title={`Check out — ${item.name}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnPrimary} disabled={busy} onClick={submit}>Check out</button>
        </>
      }
    >
      <div className={s.formGrid}>
        <Field label="Name" error={errors.user}>
          <input className={s.input} value={form.user} onChange={(e) => set("user", e.target.value)} />
        </Field>
        <Field label="Email">
          <input className={s.input} value={form.email} onChange={(e) => set("email", e.target.value)} />
        </Field>
        <Field label={`Quantity (${avail} available)`} error={errors.qty}>
          <input className={s.input} type="number" min={1} max={Math.max(avail, 1)} value={form.qty}
            onChange={(e) => set("qty", Number(e.target.value))} />
        </Field>
        <Field label="Expected return" error={errors.expectedReturn}>
          <input className={s.input} type="date" value={form.expectedReturn}
            onChange={(e) => set("expectedReturn", e.target.value)} />
        </Field>
        <div className={s.fieldFull}>
          <Field label="Purpose">
            <input className={s.input} value={form.purpose} onChange={(e) => set("purpose", e.target.value)} />
          </Field>
        </div>
        <div className={s.fieldFull}>
          <span className={s.muted}>
            {rosterLine
              ? `Recorded from the roster: ${rosterLine}`
              : "No roster entry matches this email — student ID and group will be recorded as blank."}
          </span>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
export function ReturnModal({
  item,
  loans,
  onSubmit,
  onClose,
  submitLabel,
}: {
  item: InvItem;
  loans: InvTx[];
  onSubmit: (loan: InvTx, condAfter: string) => void;
  onClose: () => void;
  /** Personal-view on-behalf labelling: a manager closing someone else's
   * loan sees "Return for {name}" on the confirm button (the chosen
   * consistent treatment — same wording as the PLAXIS release). Absent
   * (flag off), the button reads "Record return" exactly as today. */
  submitLabel?: (loan: InvTx) => string;
}) {
  const [loanId, setLoanId] = useState(loans[0]?.id || "");
  const cond = useField(item.condition || "Good");
  const [busy, fireOnce] = useSubmitOnce();
  const loan = loans.find((l) => l.id === loanId);

  return (
    <Modal
      title={`Return — ${item.name}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnPrimary} disabled={!loan || busy}
            onClick={() => loan && fireOnce(() => onSubmit(loan, cond.value))}>
            {loan && submitLabel ? submitLabel(loan) : "Record return"}
          </button>
        </>
      }
    >
      <Field label="Open loan">
        <select className={s.select} value={loanId} onChange={(e) => setLoanId(e.target.value)}>
          {loans.map((l) => (
            <option key={l.id} value={l.id}>
              {l.user} — {l.qty ?? 1} since {fmtDate(l.ts)} (due {fmtDate(l.expectedReturn)})
            </option>
          ))}
        </select>
      </Field>
      <Field label="Condition after">
        <select className={s.select} value={cond.value} onChange={(e) => cond.setValue(e.target.value)}>
          {CONDITIONS.map((c) => <option key={c}>{c}</option>)}
        </select>
      </Field>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
export function AdjustModal({
  item,
  onSubmit,
  onClose,
}: {
  item: InvItem;
  onSubmit: (delta: number, note: string) => void;
  onClose: () => void;
}) {
  const [delta, setDelta] = useState("0");
  const note = useField("");
  const [error, setError] = useState("");
  const [busy, fireOnce] = useSubmitOnce();

  const submit = () => {
    const n = Number(delta);
    if (!Number.isInteger(n) || n === 0) {
      setError("A non-zero whole number (negative removes stock).");
      return;
    }
    if ((item.qty ?? 0) + n < 0) {
      setError(`Cannot go below zero (currently ${item.qty ?? 0}).`);
      return;
    }
    fireOnce(() => onSubmit(n, note.value));
  };

  return (
    <Modal
      title={`Adjust stock — ${item.name}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnPrimary} disabled={busy} onClick={submit}>Apply</button>
        </>
      }
    >
      <Field label={`Change (current ${item.qty ?? 0} ${item.unit || ""})`} error={error}>
        <input className={s.input} type="number" value={delta} onChange={(e) => setDelta(e.target.value)} />
      </Field>
      <Field label="Reason">
        <input className={s.input} value={note.value} onChange={(e) => note.setValue(e.target.value)} />
      </Field>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
export function DamageModal({
  item,
  photosEnabled = false,
  onSubmit,
  onClose,
}: {
  item: InvItem;
  photosEnabled?: boolean;
  onSubmit: (condAfter: string, note: string, photo?: File | null) => void;
  onClose: () => void;
}) {
  const cond = useField("Damaged");
  const note = useField("");
  const [photo, setPhoto] = useState<File | null>(null);
  const [photoError, setPhotoError] = useState("");
  const [busy, fireOnce] = useSubmitOnce();
  const pick = (f: File | undefined) => {
    if (!f) { setPhoto(null); setPhotoError(""); return; }
    const err = validateImageFile(f);
    setPhotoError(err);
    setPhoto(err ? null : f);
  };
  return (
    <Modal
      title={`Report damage — ${item.name}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnDanger} disabled={!!photoError || busy}
            onClick={() => fireOnce(() => onSubmit(cond.value, note.value, photo))}>
            Record damage
          </button>
        </>
      }
    >
      <Field label="Condition">
        <select className={s.select} value={cond.value} onChange={(e) => cond.setValue(e.target.value)}>
          {CONDITIONS.map((c) => <option key={c}>{c}</option>)}
        </select>
      </Field>
      <Field label="What happened?">
        <textarea className={s.textarea} rows={3} value={note.value}
          onChange={(e) => note.setValue(e.target.value)} />
      </Field>
      {photosEnabled && (
        <Field label="Photo (optional · JPEG, PNG or WebP · 10 MB)" error={photoError}>
          <input className={s.input} type="file" accept="image/jpeg,image/png,image/webp"
            onChange={(e) => pick(e.target.files?.[0])} />
        </Field>
      )}
    </Modal>
  );
}

// ---------------------------------------------------------------------------
const ITEM_TEXT_FIELDS: [keyof InvItem, string][] = [
  ["category", "Category"], ["subCategory", "Sub-category"], ["manufacturer", "Manufacturer"],
  ["model", "Model"], ["serial", "Serial"], ["unit", "Unit"], ["location", "Location"],
  ["custodian", "Custodian"], ["supplier", "Supplier"],
];

export function ItemModal({
  item,
  onSubmit,
  onClose,
}: {
  item: InvItem | null; // null = create
  onSubmit: (fields: Partial<InvItem>) => void;
  onClose: () => void;
}) {
  const [fields, setFields] = useState<Partial<InvItem>>(
    item ?? { kind: "equipment", status: "Available", condition: "Good", qty: 1, minStock: 0 },
  );
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, fireOnce] = useSubmitOnce();
  const set = (k: keyof InvItem, v: unknown) => setFields((f) => ({ ...f, [k]: v }));

  const submit = () => {
    const e: Record<string, string> = {};
    if (!String(fields.name || "").trim()) e.name = "A name is required.";
    const qty = Number(fields.qty ?? 0);
    if (!Number.isInteger(qty) || qty < 0) e.qty = "Zero or more.";
    setErrors(e);
    if (Object.keys(e).length === 0) fireOnce(() => onSubmit(fields));
  };

  return (
    <Modal
      title={item ? `Edit — ${item.name}` : "New item"}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnPrimary} disabled={busy} onClick={submit}>
            {item ? "Save changes" : "Add item"}
          </button>
        </>
      }
    >
      <div className={s.formGrid}>
        <div className={s.fieldFull}>
          <Field label="Name" error={errors.name}>
            <input className={s.input} value={String(fields.name ?? "")}
              onChange={(e) => set("name", e.target.value)} />
          </Field>
        </div>
        <Field label="Kind">
          <select className={s.select} value={String(fields.kind ?? "equipment")}
            onChange={(e) => set("kind", e.target.value)}>
            {KINDS.map((k) => <option key={k}>{k}</option>)}
          </select>
        </Field>
        <Field label="Status">
          <select className={s.select} value={String(fields.status ?? "Available")}
            onChange={(e) => set("status", e.target.value)}>
            {STATUSES.map((k) => <option key={k}>{k}</option>)}
          </select>
        </Field>
        <Field label="Quantity" error={errors.qty}>
          <input className={s.input} type="number" min={0} value={Number(fields.qty ?? 0)}
            onChange={(e) => set("qty", Number(e.target.value))} />
        </Field>
        <Field label="Condition">
          <select className={s.select} value={String(fields.condition ?? "Good")}
            onChange={(e) => set("condition", e.target.value)}>
            {CONDITIONS.map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        {ITEM_TEXT_FIELDS.map(([key, label]) => (
          <Field key={key} label={label}>
            <input className={s.input} value={String(fields[key] ?? "")}
              onChange={(e) => set(key, e.target.value)} />
          </Field>
        ))}
        <Field label="Min stock">
          <input className={s.input} type="number" min={0} value={Number(fields.minStock ?? 0)}
            onChange={(e) => set("minStock", Number(e.target.value))} />
        </Field>
        <Field label="Maintenance interval (days)">
          <input className={s.input} type="number" min={0} value={Number(fields.maintDays ?? 0)}
            onChange={(e) => set("maintDays", Number(e.target.value))} />
        </Field>
        <Field label="Last maintenance">
          <input className={s.input} type="date" value={String(fields.lastMaint ?? "").slice(0, 10)}
            onChange={(e) => set("lastMaint", e.target.value || null)} />
        </Field>
        <Field label="Expiry date">
          <input className={s.input} type="date" value={String(fields.expiryDate ?? "").slice(0, 10)}
            onChange={(e) => set("expiryDate", e.target.value || null)} />
        </Field>
        <div className={s.fieldFull}>
          <Field label="Description">
            <textarea className={s.textarea} rows={2} value={String(fields.description ?? "")}
              onChange={(e) => set("description", e.target.value)} />
          </Field>
        </div>
        <div className={s.fieldFull}>
          <Field label="Notes">
            <textarea className={s.textarea} rows={2} value={String(fields.notes ?? "")}
              onChange={(e) => set("notes", e.target.value)} />
          </Field>
        </div>
        <div className={s.fieldFull}>
          <span className={s.muted}>
            Next service (derived on save from last maintenance + interval):{" "}
            {nextMaint({ lastMaint: String(fields.lastMaint ?? "") || null, maintDays: Number(fields.maintDays ?? 0) })
              ?.toLocaleDateString("en-CA") || "—"}
          </span>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
export function ReserveModal({
  item,
  prefill,
  tx,
  res,
  onSubmit,
  onClose,
}: {
  item: InvItem;
  prefill: SessionPrefill;
  tx: InvTx[];
  res: InvRes[];
  onSubmit: (fields: Partial<InvRes>) => void;
  onClose: () => void;
}) {
  // No group field: the server resolves it from the roster by the named
  // person — a client-supplied value would be overwritten anyway.
  const [fields, setFields] = useState<Partial<InvRes>>({
    user: prefill.name,
    start: "",
    end: "",
    purpose: "",
    qty: 1,
  });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, fireOnce] = useSubmitOnce();
  const set = (k: keyof InvRes, v: string | number) => setFields((f) => ({ ...f, [k]: v }));

  // Overlap pre-check (the server is the gate; this shows it before submit).
  const conflict = useMemo(() => {
    const start = fields.start ? new Date(fields.start) : null;
    const end = fields.end ? new Date(fields.end) : null;
    if (!start || !end || Number.isNaN(start.getTime()) || Number.isNaN(end.getTime()) || end <= start) return "";
    return conflictLine(reservationConflicts(item, start, end, Number(fields.qty) || 1, tx, res));
  }, [fields.start, fields.end, fields.qty, item, tx, res]);

  const submit = () => {
    const e: Record<string, string> = {};
    if (!String(fields.user || "").trim()) e.user = "Who is it for?";
    if (!fields.start) e.start = "Required.";
    if (!fields.end) e.end = "Required.";
    if (fields.start && fields.end && fields.end <= fields.start) e.end = "Must be after the start.";
    const q = Number(fields.qty);
    if (!Number.isInteger(q) || q < 1) e.qty = "At least 1.";
    else if (q > (item.qty ?? 0)) e.qty = `The lab has ${item.qty ?? 0}.`;
    if (conflict) e.end = conflict;
    setErrors(e);
    if (Object.keys(e).length === 0) fireOnce(() => onSubmit({ ...fields, qty: q }));
  };

  return (
    <Modal
      title={`Reserve — ${item.name}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnPrimary} disabled={busy} onClick={submit}>Request reservation</button>
        </>
      }
    >
      <div className={s.formGrid}>
        <div className={s.fieldFull}>
          <Field label="Name" error={errors.user}>
            <input className={s.input} value={String(fields.user ?? "")}
              onChange={(e) => set("user", e.target.value)} />
          </Field>
        </div>
        <Field label="Start" error={errors.start}>
          <input className={s.input} type="datetime-local" value={String(fields.start ?? "")}
            onChange={(e) => set("start", e.target.value)} />
        </Field>
        <Field label="End" error={errors.end}>
          <input className={s.input} type="datetime-local" value={String(fields.end ?? "")}
            onChange={(e) => set("end", e.target.value)} />
        </Field>
        <Field label={`Quantity (lab has ${item.qty ?? 0})`} error={errors.qty}>
          <input className={s.input} type="number" min={1} max={Math.max(item.qty ?? 1, 1)}
            value={Number(fields.qty ?? 1)} onChange={(e) => set("qty", Number(e.target.value))} />
        </Field>
        <Field label="Purpose">
          <input className={s.input} value={String(fields.purpose ?? "")}
            onChange={(e) => set("purpose", e.target.value)} />
        </Field>
        {conflict ? (
          <div className={s.fieldFull}>
            <span className={s.dangerText}>{conflict}. Pick another window — the server will refuse this one.</span>
          </div>
        ) : null}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
export function UserModal({
  existing,
  onSubmit,
  onClose,
}: {
  existing: InvUser | null;
  onSubmit: (fields: Partial<InvUser>) => void;
  onClose: () => void;
}) {
  const [fields, setFields] = useState<Partial<InvUser>>(existing ?? { role: "Student" });
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [busy, fireOnce] = useSubmitOnce();
  const set = (k: keyof InvUser, v: string) => setFields((f) => ({ ...f, [k]: v }));

  const submit = () => {
    const e: Record<string, string> = {};
    if (!String(fields.name || "").trim()) e.name = "A name is required.";
    if (!validStudentId(String(fields.studentId || ""))) e.studentId = "V-number looks like V00891234.";
    setErrors(e);
    if (Object.keys(e).length === 0) fireOnce(() => onSubmit(fields));
  };

  const text: [keyof InvUser, string][] = [
    ["email", "Email"], ["studentId", "Student ID"], ["role", "Role"],
    ["program", "Program"], ["group", "Group"], ["cosup", "Co-supervisor"], ["since", "Since"],
  ];

  return (
    <Modal
      title={existing ? `Edit — ${existing.name}` : "Add lab member"}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnPrimary} disabled={busy} onClick={submit}>
            {existing ? "Save" : "Add"}
          </button>
        </>
      }
    >
      <div className={s.formGrid}>
        <div className={s.fieldFull}>
          <Field label="Name" error={errors.name}>
            <input className={s.input} value={String(fields.name ?? "")}
              onChange={(e) => set("name", e.target.value)} />
          </Field>
        </div>
        {text.map(([key, label]) => (
          <Field key={key} label={label} error={key === "studentId" ? errors.studentId : undefined}>
            <input className={s.input} value={String(fields[key] ?? "")}
              onChange={(e) => set(key, e.target.value)} />
          </Field>
        ))}
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
export function PlaxisModal({
  sessions,
  prefill,
  onSubmit,
  onClose,
}: {
  sessions: InvPlaxis[];
  prefill: SessionPrefill;
  onSubmit: (fields: Partial<InvPlaxis>) => void;
  onClose: () => void;
}) {
  const today = new Date().toLocaleDateString("en-CA");
  const [seat, setSeat] = useState(0);
  const [date, setDate] = useState(today);
  const [from, setFrom] = useState("09:00");
  const [to, setTo] = useState("12:00");
  const user = useField(prefill.name);
  const purpose = useField("");
  const [error, setError] = useState("");
  const [busy, fireOnce] = useSubmitOnce();

  const window = useMemo(() => {
    const start = new Date(`${date}T${from}`);
    const end = new Date(`${date}T${to}`);
    return { start, end, ok: !Number.isNaN(start.getTime()) && end > start };
  }, [date, from, to]);

  const submit = () => {
    if (!user.value.trim()) return setError("Who is using the seat?");
    if (!window.ok) return setError("The end time must be after the start.");
    const clash = seatConflicts(sessions, seat, window.start, window.end);
    if (clash.length > 0) {
      // Presentation-only guard for the 2-concurrent-seat cap: this seat is
      // taken for the window (end the stale session first if it overran).
      return setError(`Seat ${seat + 1} is taken then (${clash[0].user}). Pick the other seat or another time.`);
    }
    setError("");
    // No group key: the server resolves it from the roster by the named
    // person — a client-supplied value would be overwritten anyway. This
    // pre-check is presentation only; the SERVER seat gate is the refusal.
    fireOnce(() => onSubmit({
      seat,
      user: user.value,
      purpose: purpose.value,
      start: window.start.toISOString(),
      end: window.end.toISOString(),
    }));
  };

  return (
    <Modal
      title="Book a PLAXIS seat"
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Cancel</button>
          <button type="button" className={s.btnPrimary} disabled={busy} onClick={submit}>Start session</button>
        </>
      }
    >
      <div className={s.formGrid}>
        <Field label="Seat (2 concurrent)">
          <select className={s.select} value={seat} onChange={(e) => setSeat(Number(e.target.value))}>
            <option value={0}>Seat 1</option>
            <option value={1}>Seat 2</option>
          </select>
        </Field>
        <Field label="Date">
          <input className={s.input} type="date" value={date} onChange={(e) => setDate(e.target.value)} />
        </Field>
        <Field label="From">
          <input className={s.input} type="time" value={from} onChange={(e) => setFrom(e.target.value)} />
        </Field>
        <Field label="To">
          <input className={s.input} type="time" value={to} onChange={(e) => setTo(e.target.value)} />
        </Field>
        <div className={s.fieldFull}>
          <Field label="Name">
            <input className={s.input} value={user.value} onChange={(e) => user.setValue(e.target.value)} />
          </Field>
        </div>
        <div className={s.fieldFull}>
          <Field label="Purpose" error={error}>
            <input className={s.input} value={purpose.value} onChange={(e) => purpose.setValue(e.target.value)} />
          </Field>
        </div>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------------------
// Export: preview the generated CSV and its row count BEFORE download (the
// prototype's ExportModal) — every Reports export routes through here.
// ---------------------------------------------------------------------------
export function ExportModal({
  payload,
  onClose,
}: {
  payload: ExportPayload;
  onClose: () => void;
}) {
  const [format, setFormat] = useState<"csv" | "xlsx">("csv");
  const base = `linlab-${payload.title.replace(/\s+/g, "_").toLowerCase()}-${new Date().toLocaleDateString("en-CA")}`;
  const filename = `${base}.${format}`;
  const download = () => {
    if (format === "xlsx") {
      downloadXlsx(filename, payload.report);
      toast("Excel file downloaded");
    } else {
      downloadCSV(filename, payload.csv);
      toast("CSV downloaded");
    }
  };
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(payload.csv);
      toast("Copied to clipboard");
    } catch {
      toast("Select the text and copy manually.");
    }
  };
  return (
    <Modal
      title={`Export — ${payload.title}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" className={s.btnGhost} onClick={onClose}>Close</button>
          <button type="button" className={s.btn} onClick={copy}>Copy</button>
          <button type="button" className={s.btnPrimary} onClick={download} disabled={payload.rows === 0}>
            Download {format.toUpperCase()}
          </button>
        </>
      }
    >
      <div className={s.segmented} role="group" aria-label="Export format">
        <button type="button" className={`${s.segment} ${format === "csv" ? s.segmentActive : ""}`}
          aria-pressed={format === "csv"} onClick={() => setFormat("csv")}>CSV</button>
        <button type="button" className={`${s.segment} ${format === "xlsx" ? s.segmentActive : ""}`}
          aria-pressed={format === "xlsx"} onClick={() => setFormat("xlsx")}>Excel (.xlsx)</button>
      </div>
      <textarea className={s.preview} readOnly value={payload.csv} rows={14} aria-label="Export preview" />
      <span className={s.muted}>
        {payload.rows} row{payload.rows === 1 ? "" : "s"} · {filename}
        {format === "xlsx" ? " · same rows as the preview, one sheet, auto-width columns." : " · opens cleanly in Excel."}
      </span>
    </Modal>
  );
}
