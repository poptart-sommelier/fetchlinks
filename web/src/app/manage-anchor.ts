/**
 * How a Manage action comes back to the card it was made on.
 *
 * A server action causes a fresh page load, which otherwise starts at the top
 * with every panel shut. Curation means working down a page, so losing the
 * reader's place after every click makes it unusable beyond the first screen.
 *
 * The fragment scrolls back without JavaScript. Fragments never reach the
 * server, though, so reopening the panel needs a query parameter as well.
 */

export const MANAGED_PARAM = "managed";

export function postAnchorId(postId: number): string {
  return `post-${postId}`;
}

/** Invalid input merely leaves every panel closed, which is their safe default. */
export function managedPostIdFrom(
  value: string | string[] | undefined,
): number | undefined {
  const raw = Array.isArray(value) ? value[0] : value;

  if (!raw) {
    return undefined;
  }

  const parsed = Number(raw);
  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}

/** Preserve filters and pagination while pointing the return path at one card. */
export function withManagedAnchor(path: string, postId: number): string {
  const url = new URL(path, "http://fetchlinks.invalid");

  url.searchParams.set(MANAGED_PARAM, String(postId));
  url.hash = postAnchorId(postId);

  return `${url.pathname}${url.search}${url.hash}`;
}
