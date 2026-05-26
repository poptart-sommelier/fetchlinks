/**
 * Return a safe http(s) URL string for use in `<a href>`, or `null`
 * if the value is missing, malformed, or uses an unsafe scheme
 * (e.g. `javascript:`, `data:`, `vbscript:`, `file:`).
 *
 * Bare host-like strings (no scheme) are upgraded to `https://`.
 */
export function safeExternalHref(value: string | null | undefined): string | null {
  if (value == null) return null;
  const trimmed = value.trim();
  if (!trimmed) return null;

  try {
    const url = new URL(trimmed);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return url.toString();
    }
    return null;
  } catch {
    // Bare host or host+path with no scheme: only upgrade things that
    // look like a hostname character class. Reject anything containing
    // characters that could be interpreted as a scheme separator.
    if (!/^[a-z0-9.-]+(?:[:/?#][\w./?#=%&+\-]*)?$/i.test(trimmed)) {
      return null;
    }
    if (trimmed.includes(":")) return null;
    try {
      const url = new URL(`https://${trimmed}`);
      return url.toString();
    } catch {
      return null;
    }
  }
}
