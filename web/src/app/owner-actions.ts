"use server";

import { revalidatePath } from "next/cache";
import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import type { PostSummary } from "../models/read-models";
import { resolveCatalogSource } from "../server/catalog-sources";
import {
  clearMute,
  clearThumbsDown,
  setMute,
  setThumbsDown,
  type CurationTarget,
} from "../server/curation";
import { curationTargetsFor } from "../server/curation-targets";
import { getPosts } from "../server/db";
import { restoreRssFeed, softDeleteRssFeed } from "../server/feeds";
import {
  OWNER_COOKIE_NAME,
  isValidOwnerToken,
  safeReturnPath,
} from "../server/owner";
import { getSqlClient, type SqlClient } from "../server/sql";
import {
  restoreSubreddit,
  softDeleteSubreddit,
} from "../server/subreddits";
import { withManagedAnchor } from "./manage-anchor";

/**
 * Leaving owner mode only drops the cookie; there is no server-side session.
 * Flightdeck's Basic authentication is separate and remains untouched.
 */
export async function exitOwnerModeAction(formData: FormData): Promise<void> {
  const store = await cookies();

  store.delete(OWNER_COOKIE_NAME);
  redirect(safeReturnPath(String(formData.get("next") ?? "/")));
}

/**
 * Add or clear one article's thumbs-down evidence.
 *
 * A server action is a public endpoint, so authorization is repeated here
 * rather than inherited from the page that rendered the button. The submitted
 * target is also rebuilt from the stored article: a forged form cannot invent a
 * target or save an attacker-chosen label that would later be shown to the
 * owner.
 *
 * The redirect returns to this card and reopens Manage. Losing the reader's
 * place after every click made the first curation interface unusable on a long
 * page, so the fragment and query marker are part of the action's contract.
 */
export async function thumbsDownAction(formData: FormData): Promise<void> {
  const store = await cookies();

  if (!(await isValidOwnerToken(store.get(OWNER_COOKIE_NAME)?.value))) {
    throw new Error("Owner mode is required to manage articles.");
  }

  const uniqueId = String(formData.get("post_unique_id") ?? "").trim();
  const targetType = String(formData.get("target_type") ?? "");
  const targetKey = String(formData.get("target_key") ?? "");
  const intent = String(formData.get("intent") ?? "");
  const next = safeReturnPath(String(formData.get("next") ?? "/"));

  if (!uniqueId) {
    throw new Error("Feedback needs the article it came from.");
  }

  const sql = getSqlClient(process.env);
  const { post, target } = await resolveTarget(
    sql,
    uniqueId,
    targetType,
    targetKey,
  );

  if (intent === "set") {
    await setThumbsDown(sql, { postUniqueId: uniqueId, target });
  } else if (intent === "clear") {
    await clearThumbsDown(sql, { postUniqueId: uniqueId, target });
  } else {
    throw new Error(`Unknown Manage action: ${intent}`);
  }

  revalidatePath("/");
  redirect(withManagedAnchor(next, post.id));
}

/** Apply or remove one explicit mute after rebuilding its target server-side. */
export async function muteAction(formData: FormData): Promise<void> {
  const store = await cookies();

  if (!(await isValidOwnerToken(store.get(OWNER_COOKIE_NAME)?.value))) {
    throw new Error("Owner mode is required to manage articles.");
  }

  const uniqueId = String(formData.get("post_unique_id") ?? "").trim();
  const targetType = String(formData.get("target_type") ?? "");
  const targetKey = String(formData.get("target_key") ?? "");
  const intent = String(formData.get("intent") ?? "");
  const next = safeReturnPath(String(formData.get("next") ?? "/"));

  if (!uniqueId) {
    throw new Error("Muting needs the article the target came from.");
  }

  const sql = getSqlClient(process.env);
  const { post, target } = await resolveTarget(
    sql,
    uniqueId,
    targetType,
    targetKey,
  );

  if (intent === "mute") {
    await setMute(sql, target);
  } else if (intent === "unmute") {
    await clearMute(sql, target);
  } else {
    throw new Error(`Unknown Manage action: ${intent}`);
  }

  revalidatePath("/");
  redirect(withManagedAnchor(next, post.id));
}

/** Remove or restore a catalog source after rebuilding its channel target. */
export async function sourceCollectionAction(formData: FormData): Promise<void> {
  const store = await cookies();

  if (!(await isValidOwnerToken(store.get(OWNER_COOKIE_NAME)?.value))) {
    throw new Error("Owner mode is required to manage collection sources.");
  }

  const uniqueId = String(formData.get("post_unique_id") ?? "").trim();
  const targetType = String(formData.get("target_type") ?? "");
  const targetKey = String(formData.get("target_key") ?? "");
  const intent = String(formData.get("intent") ?? "");
  const next = safeReturnPath(String(formData.get("next") ?? "/"));

  if (!uniqueId) {
    throw new Error("Source management needs the article it came from.");
  }

  const sql = getSqlClient(process.env);
  const { post, target } = await resolveTarget(
    sql,
    uniqueId,
    targetType,
    targetKey,
  );
  const source = await resolveCatalogSource(sql, target);
  let changed: boolean;

  if (intent === "remove") {
    if (source.status !== "active") {
      throw new Error("Only an active catalog source can be removed.");
    }
    changed =
      source.kind === "rss"
        ? await softDeleteRssFeed(sql, source.id)
        : await softDeleteSubreddit(sql, source.id);
  } else if (intent === "restore") {
    if (source.status !== "removed") {
      throw new Error("Only a removed catalog source can be restored.");
    }
    changed =
      source.kind === "rss"
        ? await restoreRssFeed(sql, source.id)
        : await restoreSubreddit(sql, source.id);
  } else {
    throw new Error(`Unknown source collection action: ${intent}`);
  }

  if (!changed) {
    throw new Error("The catalog source changed before this action completed.");
  }

  revalidatePath("/");
  redirect(withManagedAnchor(next, post.id));
}

async function resolveTarget(
  sql: SqlClient,
  uniqueId: string,
  targetType: string,
  targetKey: string,
): Promise<{ post: PostSummary; target: CurationTarget }> {
  const { posts } = await getPosts(sql, {
    includeMuted: true,
    uniqueId,
    pageSize: 1,
  });
  const post = posts[0];

  if (!post) {
    throw new Error("That article no longer exists.");
  }

  const target = curationTargetsFor(post).find(
    (candidate) => candidate.type === targetType && candidate.key === targetKey,
  );

  if (!target) {
    throw new Error("That target does not belong to this article.");
  }

  return { post, target };
}
