/**
 * Auth API client.
 *
 * Thin typed wrappers around the backend auth endpoints. Every call uses
 * `credentials: "include"` so the browser BOTH stores the httpOnly access_token
 * cookie returned by /auth/login AND sends it on later requests. Omitting this
 * is the classic cookie-auth bug: login returns 200 but the cookie never
 * persists, so the very next request looks unauthenticated.
 *
 * The token itself is never read or stored by JS (it is httpOnly). Only
 * non-sensitive user info (id/email/name/role) is returned for use in UI state.
 */
import { PYTHON_BACKEND_URL } from "../config/api";

export type AuthUser = {
  id: string;
  email: string;
  full_name: string | null;
  role: string;
};

export type LoginResponse = {
  access_token: string;
  token_type: string;
  user: AuthUser;
};

/** Error carrying the HTTP status so callers can branch on 401 vs 409 etc. */
export class AuthError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AuthError";
    this.status = status;
  }
}

const JSON_HEADERS = { "Content-Type": "application/json" };

/** Turn a non-OK response into a typed AuthError (using the backend's `detail`). */
async function raise(res: Response): Promise<never> {
  let detail = res.statusText;
  try {
    const body = await res.json();
    if (body && typeof body.detail === "string") detail = body.detail;
  } catch {
    // Non-JSON error body -- keep the status text.
  }
  throw new AuthError(res.status, detail);
}

export async function login(
  email: string,
  password: string,
): Promise<LoginResponse> {
  const res = await fetch(`${PYTHON_BACKEND_URL}/auth/login`, {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "include", // REQUIRED so the browser stores the httpOnly cookie
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) await raise(res);
  return res.json();
}

export async function signup(
  email: string,
  password: string,
  fullName?: string,
): Promise<AuthUser> {
  const res = await fetch(`${PYTHON_BACKEND_URL}/auth/signup`, {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "include",
    body: JSON.stringify({
      email,
      password,
      full_name: fullName && fullName.trim() ? fullName.trim() : null,
    }),
  });
  if (!res.ok) await raise(res);
  return res.json();
}

export async function logout(): Promise<void> {
  const res = await fetch(`${PYTHON_BACKEND_URL}/auth/logout`, {
    method: "POST",
    credentials: "include",
  });
  if (!res.ok) await raise(res);
}

/**
 * Request a password-reset email. The backend returns the SAME generic 200
 * whether or not the address has an account (anti-enumeration), so a resolved
 * promise means only "the request was accepted", never "the account exists".
 */
export async function forgotPassword(email: string): Promise<void> {
  const res = await fetch(`${PYTHON_BACKEND_URL}/auth/forgot-password`, {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "include",
    body: JSON.stringify({ email }),
  });
  if (!res.ok) await raise(res);
}

/** Complete a password reset with a token from the reset email. 400 means the
 * token is invalid, expired, or already used (deliberately indistinct). */
export async function resetPassword(
  token: string,
  newPassword: string,
): Promise<void> {
  const res = await fetch(`${PYTHON_BACKEND_URL}/auth/reset-password`, {
    method: "POST",
    headers: JSON_HEADERS,
    credentials: "include",
    body: JSON.stringify({ token, new_password: newPassword }),
  });
  if (!res.ok) await raise(res);
}

/** Returns the current user, or null if not authenticated (401). */
export async function getCurrentUser(): Promise<AuthUser | null> {
  const res = await fetch(`${PYTHON_BACKEND_URL}/auth/me`, {
    method: "GET",
    credentials: "include",
  });
  if (res.status === 401) return null;
  if (!res.ok) await raise(res);
  return res.json();
}
