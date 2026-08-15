/**
 * How a rating click comes back to the card it was made on.
 *
 * Rating submits a form, and a form submission is a fresh page load: it starts
 * at the top of the feed with every panel shut. Curation means judging one item
 * after another down a page, so losing your place on every click makes it
 * unusable past the first screen.
 *
 * Two halves, and they need different mechanisms. The fragment scrolls the
 * browser back to the card without any script. The fragment is *not* sent to
 * the server, though, so it cannot reopen the panel -- that needs a query
 * parameter the render can actually see, which is what `rated` is for.
 *
 * Numeric post ids rather than the unique id, because this value ends up in an
 * `id` attribute and a URL fragment, and a unique id may be an arbitrary
 * string from somebody else's feed.
 */

export const RATED_PARAM = "rated";

export function postAnchorId(postId: number): string {
  return `post-${postId}`;
}

/**
 * Read `rated` back off the URL. Anything that is not a plain post id is
 * ignored rather than rejected: the worst a bad value can do is leave every
 * panel closed, which is where they all start anyway.
 */
export function ratedPostIdFrom(
  value: string | string[] | undefined,
): number | undefined {
  const raw = Array.isArray(value) ? value[0] : value;

  if (!raw) {
    return undefined;
  }

  const parsed = Number(raw);

  return Number.isSafeInteger(parsed) && parsed > 0 ? parsed : undefined;
}

/**
 * Point a return path at one card. The path has already been through
 * `safeReturnPath`, so it is local; the throwaway base exists only to let
 * `URL` do the query-string work, and nothing of it survives into the result.
 */
export function withRatedAnchor(path: string, postId: number): string {
  const url = new URL(path, "http://fetchlinks.invalid");

  url.searchParams.set(RATED_PARAM, String(postId));
  url.hash = postAnchorId(postId);

  return `${url.pathname}${url.search}${url.hash}`;
}
