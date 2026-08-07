"use client";

import { type FormEvent, useState } from "react";
import Link from "next/link";

import { forgotPassword } from "../lib/auth";
import styles from "../components/auth.module.css";

// Shown after EVERY submit, success or failure: this page must never reveal
// whether an address has an account (matches the backend's generic 200).
const CONFIRMATION =
  "If an account exists for that address, a reset link has been sent.";

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSubmitting(true);
    try {
      await forgotPassword(email);
    } catch {
      // Deliberately swallowed: the generic confirmation below is shown
      // regardless of outcome, so neither the response nor an error can be
      // used to probe which emails are registered.
    }
    setSubmitted(true);
  }

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
          <h1 className={styles.heading}>Reset your password</h1>
          <p className={styles.subhead}>
            Enter your account email and we will send you a link to choose a
            new password.
          </p>

          {submitted ? (
            <div className={styles.notice} role="status">
              {CONFIRMATION}
            </div>
          ) : (
            <form className={styles.form} onSubmit={handleSubmit} noValidate>
              <div className={styles.field}>
                <label className={styles.label} htmlFor="email">
                  Email
                </label>
                <input
                  id="email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  autoFocus
                  required
                  className={styles.input}
                  placeholder="you@uvic.ca"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>

              <button
                type="submit"
                className={styles.button}
                disabled={submitting}
              >
                {submitting ? "Sending..." : "Send reset link"}
              </button>
            </form>
          )}

          <p className={styles.footer}>
            Remembered it?{" "}
            <Link href="/login" className={styles.link}>
              Back to log in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
