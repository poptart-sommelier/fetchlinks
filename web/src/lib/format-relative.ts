/**
 * Format an ISO-8601 timestamp as a short human-readable relative time
 * ("3m ago", "2h ago", "5d ago", "3w ago", "2mo ago", "1y ago").
 *
 * Future times return "in 3m" / "in 2h" / etc.
 *
 * Returns null when the input is null, undefined, or not parseable.
 */
export function formatRelative(
  iso: string | null | undefined,
  now: Date = new Date(),
): string | null {
  if (iso == null) return null;
  const then = new Date(iso);
  const ms = then.getTime();
  if (Number.isNaN(ms)) return null;

  const diffSec = Math.round((now.getTime() - ms) / 1000);
  const past = diffSec >= 0;
  const seconds = Math.abs(diffSec);

  const value = pickUnit(seconds);
  return past ? `${value} ago` : `in ${value}`;
}

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;
const MONTH = 30 * DAY;
const YEAR = 365 * DAY;

function pickUnit(seconds: number): string {
  if (seconds < MINUTE) return `${seconds}s`;
  if (seconds < HOUR) return `${Math.floor(seconds / MINUTE)}m`;
  if (seconds < DAY) return `${Math.floor(seconds / HOUR)}h`;
  if (seconds < WEEK) return `${Math.floor(seconds / DAY)}d`;
  if (seconds < MONTH) return `${Math.floor(seconds / WEEK)}w`;
  if (seconds < YEAR) return `${Math.floor(seconds / MONTH)}mo`;
  return `${Math.floor(seconds / YEAR)}y`;
}
