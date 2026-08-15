"use client";

/**
 * Top-bar account popover. Identity comes from the existing auth context;
 * sign-out reuses the same signOut() + redirect the old sidebar block used.
 * Account / Settings / Shortcuts / Invite have no backend behind them yet, so
 * they render disabled with a tooltip rather than inventing endpoints.
 *
 * Closes on outside click and Escape; focus returns to the avatar on close.
 */
import React, { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { LogOut, User } from "lucide-react";

import { useAuth } from "../lib/auth-context";
import s from "./account-menu.module.css";

const DISABLED_ITEMS = ["Account", "Settings", "Shortcuts", "Invite"] as const;

export default function AccountMenu() {
  const { user, signOut } = useAuth();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const btnRef = useRef<HTMLButtonElement | null>(null);
  const menuRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        btnRef.current?.focus();
      }
    };
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node;
      if (!menuRef.current?.contains(t) && !btnRef.current?.contains(t)) {
        setOpen(false);
      }
    };
    document.addEventListener("keydown", onKey);
    document.addEventListener("mousedown", onDown);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.removeEventListener("mousedown", onDown);
    };
  }, [open]);

  // Move focus into the popover when it opens so Escape/Tab work immediately.
  useEffect(() => {
    if (open) menuRef.current?.focus();
  }, [open]);

  async function handleSignOut() {
    setOpen(false);
    await signOut();
    router.replace("/login");
  }

  const displayName = user?.full_name?.trim() || user?.email || "";
  const initials = displayName
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((w) => w[0]!.toUpperCase())
    .join("");

  return (
    <div className={s.wrap}>
      <button
        ref={btnRef}
        type="button"
        className={s.avatar}
        aria-label="Account"
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        {user ? (
          <span className={s.initials}>{initials}</span>
        ) : (
          <User size={14} strokeWidth={1.5} />
        )}
      </button>
      {open && (
        <div
          ref={menuRef}
          className={s.menu}
          role="menu"
          aria-label="Account"
          tabIndex={-1}
        >
          {user && (
            <div className={s.identity}>
              <span className={s.name}>{displayName}</span>
              {user.full_name?.trim() && (
                <span className={s.email}>{user.email}</span>
              )}
              {user.role && <span className={s.roleBadge}>{user.role}</span>}
            </div>
          )}
          <div className={s.sep} aria-hidden="true" />
          {DISABLED_ITEMS.map((label) => (
            <span key={label} className={s.itemWrap} title="Not available yet">
              <button type="button" role="menuitem" className={s.item} disabled>
                {label}
              </button>
            </span>
          ))}
          <div className={s.sep} aria-hidden="true" />
          <button
            type="button"
            role="menuitem"
            className={`${s.item} ${s.signOut}`}
            onClick={handleSignOut}
          >
            <LogOut size={14} strokeWidth={1.5} />
            Sign out
          </button>
        </div>
      )}
    </div>
  );
}
