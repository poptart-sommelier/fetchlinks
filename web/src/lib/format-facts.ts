/**
 * Small formatters for the status page.
 *
 * Separate from the page because they encode judgements worth testing on their
 * own: what precision is useful, and what an absent measurement should read as.
 * Every one returns an em dash rather than "0" or "null" for a missing value,
 * because a status page that prints 0 bytes free when it simply does not know
 * is worse than one that admits it.
 */

const MISSING = "\u2014";

export function formatDuration(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return MISSING;
  if (ms < 1000) return `${Math.round(ms)}ms`;

  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;

  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds - minutes * 60);
  if (minutes < 60) return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;

  const hours = Math.floor(minutes / 60);
  return `${hours}h ${minutes - hours * 60}m`;
}

/** An age in seconds, read as "how long since". */
export function formatAge(seconds: number | null | undefined): string {
  if (seconds == null || !Number.isFinite(seconds) || seconds < 0) return MISSING;
  return formatDuration(seconds * 1000);
}

/**
 * Bytes at 1024, labelled with the units people actually say.
 *
 * Three significant figures at most: the difference between 412 MB and 412.4 MB
 * has never changed anyone's decision, and the extra digits make two numbers
 * harder to compare at a glance.
 */
export function formatBytes(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return MISSING;

  const negative = bytes < 0;
  let value = Math.abs(bytes);
  const units = ["B", "KB", "MB", "GB", "TB"];
  let unit = 0;

  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }

  const rendered = unit === 0 ? String(Math.round(value)) : trim(value);
  return `${negative ? "-" : ""}${rendered} ${units[unit]}`;
}

/** Bytes as a change, so an increase reads as one. */
export function formatByteChange(bytes: number | null | undefined): string {
  if (bytes == null || !Number.isFinite(bytes)) return MISSING;
  if (bytes === 0) return "no change";
  return `${bytes > 0 ? "+" : ""}${formatBytes(bytes)}`;
}

function trim(value: number): string {
  const digits = value >= 100 ? 0 : value >= 10 ? 1 : 2;
  return value
    .toFixed(digits)
    .replace(/(\.\d*?)0+$/, "$1")
    .replace(/\.$/, "");
}

export { MISSING };
