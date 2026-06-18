"use client";

import { type FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";

import { AuthError, signup } from "../lib/auth";
import styles from "../components/auth.module.css";

// UX-only shape check. The backend (EmailStr + hashing) is the real gate.
const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const MIN_PASSWORD_LENGTH = 8;

export default function SignupPage() {
  const router = useRouter();
  const [fullName, setFullName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function clientValidation(): string | null {
    if (!EMAIL_RE.test(email)) return "Enter a valid email address.";
    if (password.length < MIN_PASSWORD_LENGTH) {
      return `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
    }
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
      await signup(email, password, fullName);
      // No auto-login: send them to /login to sign in (proves the cookie flow).
      router.push("/login?registered=1");
    } catch (err) {
      if (err instanceof AuthError && err.status === 409) {
        setError("An account with this email already exists");
      } else {
        setError("Something went wrong. Please try again.");
      }
      setSubmitting(false);
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
          <h1 className={styles.heading}>Create account</h1>
          <p className={styles.subhead}>
            Set up an account to save your threads and upload your own documents.
          </p>

          <form className={styles.form} onSubmit={handleSubmit} noValidate>
            <div className={styles.field}>
              <label className={styles.label} htmlFor="fullName">
                Full name (optional)
              </label>
              <input
                id="fullName"
                name="fullName"
                type="text"
                autoComplete="name"
                autoFocus
                className={styles.input}
                placeholder="Jordan Lee"
                value={fullName}
                onChange={(e) => setFullName(e.target.value)}
              />
            </div>

            <div className={styles.field}>
              <label className={styles.label} htmlFor="email">
                Email
              </label>
              <input
                id="email"
                name="email"
                type="email"
                autoComplete="email"
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
                autoComplete="new-password"
                required
                className={styles.input}
                placeholder="At least 8 characters"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
              <span className={styles.assist}>Use at least 8 characters.</span>
            </div>

            {error && (
              <div className={styles.error} role="alert">
                {error}
              </div>
            )}

            <button type="submit" className={styles.button} disabled={submitting}>
              {submitting ? "Creating account..." : "Create account"}
            </button>
          </form>

          <p className={styles.footer}>
            Already have an account?{" "}
            <Link href="/login" className={styles.link}>
              Log in
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
