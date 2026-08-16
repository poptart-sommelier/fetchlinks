/**
 * Owner mode: a short-lived signed cookie that marks the reader as the site's
 * one curator, so Manage controls can appear on the main page without exposing
 * them, or the evidence behind them, to anonymous visitors.
 *
 * The signing key is derived from the Flightdeck credentials rather than being
 * a secret of its own. That is deliberate: it adds nothing new to configure,
 * and changing the Flightdeck password invalidates every outstanding owner
 * session as a side effect, which is exactly what a password change should do.
 *
 * The cookie carries only an expiry. It is not an identity, it grants nothing
 * beyond curation, and it never replaces Basic authentication for the rest of
 * Flightdeck.
 */

type Env = Partial<Record<string, string | undefined>>;

export const OWNER_COOKIE_NAME = "fetchlinks_owner";

export const OWNER_SESSION_MAX_AGE_SECONDS = 7 * 24 * 60 * 60;

const TOKEN_VERSION = "v1";

/**
 * Mint a cookie value that expires on its own. `nowMs` is injectable so the
 * expiry can be tested without waiting a week for it.
 */
export async function createOwnerToken(
  env: Env = process.env,
  nowMs: number = Date.now(),
): Promise<string | undefined> {
  const key = await deriveKey(env);

  if (!key) {
    return undefined;
  }

  const expiresAt = Math.floor(nowMs / 1000) + OWNER_SESSION_MAX_AGE_SECONDS;
  const payload = `${TOKEN_VERSION}.${expiresAt}`;

  return `${payload}.${await sign(key, payload)}`;
}

export async function isValidOwnerToken(
  token: string | undefined,
  env: Env = process.env,
  nowMs: number = Date.now(),
): Promise<boolean> {
  if (!token) {
    return false;
  }

  const parts = token.split(".");

  if (parts.length !== 3) {
    return false;
  }

  const [version, expiresAt, signature] = parts;

  if (version !== TOKEN_VERSION) {
    return false;
  }

  const key = await deriveKey(env);

  if (!key) {
    return false;
  }

  // Verify the signature before trusting the expiry: an unsigned token could
  // otherwise claim any expiry it liked and be rejected on a technicality
  // rather than on the only thing that matters.
  const expected = await sign(key, `${version}.${expiresAt}`);

  if (!safeEqual(signature, expected)) {
    return false;
  }

  const seconds = Number(expiresAt);

  return Number.isSafeInteger(seconds) && seconds * 1000 > nowMs;
}

/**
 * The path a visitor is sent back to after signing in. Anything that is not a
 * plain local path is discarded, so a crafted `next` cannot bounce the browser
 * to another site carrying the freshly issued cookie's referrer.
 */
export function safeReturnPath(value: string | null | undefined): string {
  if (!value || !value.startsWith("/")) {
    return "/";
  }

  // `//host` and `/\host` are both read as protocol-relative URLs by browsers.
  if (value.startsWith("//") || value.startsWith("/\\")) {
    return "/";
  }

  return value;
}

async function deriveKey(env: Env): Promise<CryptoKey | undefined> {
  const user = env.FETCHLINKS_ADMIN_USER?.trim();
  const pass = env.FETCHLINKS_ADMIN_PASS;

  if (!user || !pass) {
    return undefined;
  }

  return crypto.subtle.importKey(
    "raw",
    new TextEncoder().encode(`fetchlinks-owner-session\u0000${user}\u0000${pass}`),
    { name: "HMAC", hash: "SHA-256" },
    false,
    ["sign"],
  );
}

async function sign(key: CryptoKey, payload: string): Promise<string> {
  const signature = await crypto.subtle.sign(
    "HMAC",
    key,
    new TextEncoder().encode(payload),
  );

  return Array.from(new Uint8Array(signature))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }

  let result = 0;

  for (let i = 0; i < a.length; i += 1) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }

  return result === 0;
}
