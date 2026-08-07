"use client";

import { type FormEvent, useEffect, useState } from "react";
import Link from "next/link";

import { AuthError, resetPassword } from "../lib/auth";
import styles from "../components/auth.module.css";

// Mirrors the signup page's client-side rule (the backend enforces the same
// rules for both paths). Keep the two pages in sync if this ever changes.
const MIN_PASSWORD_LENGTH = 8;

export default function ResetPasswordPage() {
  const [token, setToken] = useState<string | null>(null);
  const [tokenRead, setTokenRead] = useState(false);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState(false);
  const [tokenRejected, setTokenRejected] = useState(false);

  // Read ?token= from the URL directly (rather than useSearchParams) to avoid
  // the App Router Suspense requirement -- same pattern as /login?registered=1.
  // Once captured into state, the token is stripped from the address bar so it
  // cannot linger in the browser history / be shoulder-surfed or shared via a
  // copied URL. Only the visible URL changes; submission uses the state copy.
  // A refresh after the strip therefore lands on the dead-link state below,
  // which links back to /forgot-password for a fresh token.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const urlToken = params.get("token");
    setToken(urlToken);
    setTokenRead(true);
    if (urlToken) {
      window.history.replaceState({}, "", "/reset-password");
    }
  }, []);

  function clientValidation(): string | null {
    if (password.length < MIN_PASSWORD_LENGTH) {
      return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
    }
    if (password !== confirm) return "Passwords do not match.";
    return null;
  }

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);

    const problem = clientValidation();
    if (problem) {
      setError(problem);
      return;
    }

    setSubmitting(true);
    try {
      await resetPassword(token ?? "", password);
      setDone(true);
    } catch (err) {
      if (err instanceof AuthError && err.status === 400) {
        // Invalid, expired, or already-used token -- show the dead-link state
        // with a way to request a fresh one.
        setTokenRejected(true);
      } else if (err instanceof AuthError && err.status === 429) {
        setError("Too many attempts. Please wait a moment and try again.");
      } else {
        setError("Something went wrong. Please try again.");
      }
      setSubmitting(false);
    }
  }

  const invalidLink = tokenRead && (!token || tokenRejected);

  return (
    <div className={styles.page}>
      <div className={styles.card}>
        <div className={styles.strata} aria-hidden="true">
          <span />
          <span />
          <span />
          <span />
          <span />
        </div>

        <div className={styles.body}>
          <div className={styles.eyebrow}>Geotechnical Assistant</div>
          <h1 className={styles.heading}>Choose a new password</h1>

          {done ? (
            <>
              <div className={styles.notice} role="status">
                Your password has been reset. Please log in with your new
                password.
              </div>
              <p className={styles.footer}>
                <Link href="/login" className={styles.link}>
                  Go to log in
                </Link>
              </p>
            </>
          ) : invalidLink ? (
            <>
              <div className={styles.error} role="alert">
                This reset link is invalid, has expired, or was already used.
              </div>
              <p className={styles.footer}>
                <Link href="/forgot-password" className={styles.link}>
                  Request a new reset link
                </Link>
              </p>
            </>
          ) : !tokenRead ? null : (
            <>
              <p className={styles.subhead}>
                Enter a new password for your account.
              </p>

              <form className={styles.form} onSubmit={handleSubmit} noValidate>
                <div className={styles.field}>
                  <label className={styles.label} htmlFor="password">
                    New password
                  </label>
                  <input
                    id="password"
                    name="password"
                    type="password"
                    autoComplete="new-password"
                    autoFocus
                    required
                    className={styles.input}
                    placeholder="At least 8 characters"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                  <span className={styles.assist}>
                    Use at least 8 characters.
                  </span>
                </div>

                <div className={styles.field}>
                  <label className={styles.label} htmlFor="confirm">
                    Confirm new password
                  </label>
                  <input
                    id="confirm"
                    name="confirm"
                    type="password"
                    autoComplete="new-password"
                    required
                    className={styles.input}
                    placeholder="Repeat the new password"
                    value={confirm}
                    onChange={(e) => setConfirm(e.target.value)}
                  />
                </div>

                {error && (
                  <div className={styles.error} role="alert">
                    {error}
                  </div>
                )}

                <button
                  type="submit"
                  className={styles.button}
                  disabled={submitting}
                >
                  {submitting ? "Resetting..." : "Reset password"}
                </button>
              </form>

              <p className={styles.footer}>
                <Link href="/login" className={styles.link}>
                  Back to log in
                </Link>
              </p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
