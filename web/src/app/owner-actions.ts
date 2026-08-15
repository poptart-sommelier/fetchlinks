"use server";

import { cookies } from "next/headers";
import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { getPosts } from "../server/db";
import type { PostSummary } from "../models/read-models";
import {
  OWNER_COOKIE_NAME,
  isValidOwnerToken,
  safeReturnPath,
} from "../server/owner";
import { ratingTargetsFor } from "../server/rating-targets";
import {
  clearRating,
  isRatingVerdict,
  rateTarget,
  type RatingTarget,
} from "../server/ratings";
import { getSqlClient } from "../server/sql";
import { withRatedAnchor } from "./post-anchor";

/**
 * Leaving owner mode only has to drop the cookie; there is no server-side
 * session to forget. Basic authentication for the rest of Flightdeck is
 * untouched, because the two were never the same thing.
 */
export async function exitOwnerModeAction(formData: FormData): Promise<void> {
  const store = await cookies();

  store.delete(OWNER_COOKIE_NAME);

  redirect(safeReturnPath(String(formData.get("next") ?? "/")));
}

/**
 * Record or clear one verdict.
 *
 * Authorization is checked here rather than inherited from the page that drew
 * the button: a server action is a public endpoint, and the absence of a
 * control in the rendered HTML stops nobody from calling it.
 *
 * The target is likewise not taken at face value. The submitted post is loaded
 * and its rateable targets rebuilt from the database; a target that is not
 * among them is rejected. That closes two holes at once -- rating something
 * the post has nothing to do with, and attaching an attacker-chosen label to a
 * real target, which would otherwise be stored and later displayed in the
 * owner's own review queue.
 *
 * The redirect goes back to the card rather than the top of the feed. See
 * `post-anchor.ts` for why that takes both a fragment and a query parameter.
 */
export async function rateAction(formData: FormData): Promise<void> {
  const store = await cookies();

  if (!(await isValidOwnerToken(store.get(OWNER_COOKIE_NAME)?.value))) {
    throw new Error("Owner mode is required to rate.");
  }

  const uniqueId = String(formData.get("post_unique_id") ?? "").trim();
  const targetType = String(formData.get("target_type") ?? "");
  const targetKey = String(formData.get("target_key") ?? "");
  const verdict = String(formData.get("verdict") ?? "");
  const next = safeReturnPath(String(formData.get("next") ?? "/"));

  if (!uniqueId) {
    throw new Error("A rating needs the post it came from.");
  }

  const sql = getSqlClient(process.env);
  const { post, target } = await resolveTarget(uniqueId, targetType, targetKey);

  if (verdict === "clear") {
    await clearRating(sql, target);
  } else if (isRatingVerdict(verdict)) {
    await rateTarget(sql, { target, verdict, postUniqueId: uniqueId });
  } else {
    throw new Error(`Unknown verdict: ${verdict}`);
  }

  revalidatePath("/");
  redirect(withRatedAnchor(next, post.id));
}

async function resolveTarget(
  uniqueId: string,
  targetType: string,
  targetKey: string,
): Promise<{ post: PostSummary; target: RatingTarget }> {
  const sql = getSqlClient(process.env);
  const { posts } = await getPosts(sql, { uniqueId, pageSize: 1 });
  const post = posts[0];

  if (!post) {
    throw new Error("That post no longer exists.");
  }

  const target = ratingTargetsFor(post).find(
    (candidate) => candidate.type === targetType && candidate.key === targetKey,
  );

  if (!target) {
    throw new Error("That is not something this post can be rated by.");
  }

  return { post, target };
}
