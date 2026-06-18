"use client";

import { type FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { AuthError, login } from "../lib/auth";
import styles from "../components/auth.module.css";

export default function LoginPage() {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [justRegistered, setJustRegistered] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  // Signup redirects here as /login?registered=1. Read it from the URL directly
  // (rather than useSearchParams) to avoid the App Router Suspense requirement.
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    if (params.get("registered") === "1") setJustRegistered(true);
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
      // Cookie is now set; go to the chat page. refresh() re-runs server data.
      router.push("/");
      router.refresh();
    } catch (err) {
      if (err instanceof AuthError && err.status === 401) {
        setError("Incorrect email or password");
      } else {
        setError("Something went wrong. Please try again.");
      }
      setSubmitting(false); // re-enable; on success we navigate away instead
    }
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
          <h1 className={styles.heading}>Log in</h1>
          <p className={styles.subhead}>
            Sign in to reach your saved threads and uploaded documents.
          </p>

          {justRegistered && (
            <div className={styles.notice} role="status">
              Account created. Please log in.
            </div>
          )}

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

            <div className={styles.field}>
              <label className={styles.label} htmlFor="password">
                Password
              </label>
              <input
                id="password"
                name="password"
                type="password"
                autoComplete="current-password"
                required
                className={styles.input}
                placeholder="Your password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>

            {error && (
              <div className={styles.error} role="alert">
                {error}
              </div>
            )}

            <button type="submit" className={styles.button} disabled={submitting}>
              {submitting ? "Logging in..." : "Log in"}
            </button>
          </form>

          <p className={styles.footer}>
            New here?{" "}
            <Link href="/signup" className={styles.link}>
              Create an account
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
