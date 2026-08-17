"use client";

/**
 * Minimal toast layer. Replaces the native alert() call sites — OS dialogs
 * can't be themed and block the thread. Fire-and-forget:
 *
 *   import { toast } from "./toaster";
 *   toast("The export failed. Try again in a moment.");
 *
 * <Toaster /> is mounted once in app/layout.tsx; it listens for the events,
 * stacks messages bottom-right, and dismisses them after a few seconds or on
 * click. No portal, no context, no dependency.
 */
import React, { useEffect, useState } from "react";
import s from "./toaster.module.css";

const EVENT = "app-toast";

export function toast(message: string) {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(EVENT, { detail: message }));
}

type Toast = { id: number; message: string };

const DISMISS_MS = 6000;

export default function Toaster() {
  const [toasts, setToasts] = useState<Toast[]>([]);

  useEffect(() => {
    const onToast = (e: Event) => {
      const message = String((e as CustomEvent).detail ?? "");
      if (!message) return;
      const id = Date.now() + Math.random();
      setToasts((prev) => [...prev, { id, message }]);
      window.setTimeout(() => {
        setToasts((prev) => prev.filter((t) => t.id !== id));
      }, DISMISS_MS);
    };
    window.addEventListener(EVENT, onToast);
    return () => window.removeEventListener(EVENT, onToast);
  }, []);

  if (toasts.length === 0) return null;

  return (
    <div className={s.stack} role="status" aria-live="polite">
      {toasts.map((t) => (
        <button
          key={t.id}
          type="button"
          className={s.toast}
          onClick={() => setToasts((prev) => prev.filter((x) => x.id !== t.id))}
          title="Dismiss"
        >
          {t.message}
        </button>
      ))}
    </div>
  );
}
