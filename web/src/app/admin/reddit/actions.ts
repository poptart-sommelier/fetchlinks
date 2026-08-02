"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { getSqlClient } from "../../../server/sql";
import {
  addSubreddit,
  restoreSubreddit,
  softDeleteSubreddit,
} from "../../../server/subreddits";

const ADMIN_PATH = "/admin/reddit";

function parseSubredditId(value: FormDataEntryValue | null): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error("Invalid subreddit id.");
  }
  return parsed;
}

export async function addSubredditAction(formData: FormData): Promise<void> {
  const name = String(formData.get("subreddit_name") ?? "");
  const result = await addSubreddit(getSqlClient(process.env), name);
  revalidatePath(ADMIN_PATH);

  const params = new URLSearchParams();
  if (result.status === "added") {
    params.set("added", "ok");
    params.set("name", result.subreddit.name);
  } else if (result.status === "exists") {
    params.set("added", "exists");
    params.set("name", result.subreddit.name);
  } else {
    params.set("added", "invalid");
    params.set("reason", result.reason);
    if (name.trim()) params.set("name", name.trim());
  }
  redirect(`${ADMIN_PATH}?${params.toString()}`);
}

export async function deleteSubredditAction(
  formData: FormData,
): Promise<void> {
  const subredditId = parseSubredditId(formData.get("subreddit_id"));
  await softDeleteSubreddit(getSqlClient(process.env), subredditId);
  revalidatePath(ADMIN_PATH);
}

export async function restoreSubredditAction(
  formData: FormData,
): Promise<void> {
  const subredditId = parseSubredditId(formData.get("subreddit_id"));
  await restoreSubreddit(getSqlClient(process.env), subredditId);
  revalidatePath(ADMIN_PATH);
}
