"use client";

/**
 * Inventory primitives. The app deliberately has no shared component library
 * (chat / kb-upload / workspace each build bespoke elements in their own CSS
 * module on the global tokens), so the prototype's Modal / Field / Chip /
 * Empty are rebuilt here the same way. The one true shared primitive — the
 * toast — is reused from components/toaster.
 */
import React, { useEffect, useRef } from "react";

import s from "../inventory.module.css";
import { STATUS_LABEL, StatusKey } from "../lib";

// --- Eyebrow: the app's mono-uppercase treatment ---------------------------
export function Eyebrow({ children }: { children: React.ReactNode }) {
  return <div className={s.eyebrow}>{children}</div>;
}

// --- Status chip: fg/bg token pair per status; label always rendered so
// color is never the only carrier of state. ---------------------------------
export function Chip({ status, label }: { status: StatusKey; label?: string }) {
  return (
    <span className={`${s.chip} ${s[`chip_${status}`]}`}>
      {label ?? STATUS_LABEL[status]}
    </span>
  );
}

// --- Severity dot for alerts: crim (danger) > rust (oxide) > amber (warn) --
export function SeverityDot({ severity }: { severity: string }) {
  const key = severity === "high" ? "high" : severity === "medium" ? "medium" : "low";
  return <span className={`${s.sevDot} ${s[`sev_${key}`]}`} aria-hidden="true" />;
}

// --- Empty state ------------------------------------------------------------
export function Empty({ children }: { children: React.ReactNode }) {
  return <div className={s.empty}>{children}</div>;
}

// --- KPI card ---------------------------------------------------------------
export function Kpi({
  label,
  value,
  tone,
}: {
  label: string;
  value: React.ReactNode;
  tone?: "danger" | "warn" | "accent";
}) {
  return (
    <div className={s.kpi}>
      <div className={`${s.kpiValue} ${tone ? s[`kpiValue_${tone}`] : ""}`}>{value}</div>
      <div className={s.kpiLabel}>{label}</div>
    </div>
  );
}

// --- Field: label + control + inline error ---------------------------------
export function Field({
  label,
  error,
  children,
}: {
  label: string;
  error?: string;
  children: React.ReactNode;
}) {
  return (
    <label className={s.field}>
      <span className={s.fieldLabel}>{label}</span>
      {children}
      {error ? <span className={s.fieldError}>{error}</span> : null}
    </label>
  );
}

// --- Modal: overlay + dialog on --s2 / --e3; Escape and overlay-click close;
// focus moves into the dialog on open. --------------------------------------
export function Modal({
  title,
  onClose,
  children,
  actions,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
  actions?: React.ReactNode;
}) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => document.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div
      className={s.modalOverlay}
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        ref={ref}
        className={s.modal}
        role="dialog"
        aria-modal="true"
        aria-label={title}
        tabIndex={-1}
      >
        <div className={s.modalTitle}>{title}</div>
        <div className={s.modalBody}>{children}</div>
        {actions ? <div className={s.modalActions}>{actions}</div> : null}
      </div>
    </div>
  );
}
