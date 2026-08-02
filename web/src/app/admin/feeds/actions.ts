"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";

import { getSqlClient } from "../../../server/sql";
import {
  addRssFeed,
  restoreRssFeed,
  softDeleteRssFeed,
} from "../../../server/feeds";

const ADMIN_PATH = "/admin/feeds";

function parseFeedId(value: FormDataEntryValue | null): number {
  const parsed = Number(value);
  if (!Number.isInteger(parsed) || parsed < 1) {
    throw new Error("Invalid feed id.");
  }
  return parsed;
}

export async function addFeedAction(formData: FormData): Promise<void> {
  const url = String(formData.get("feed_url") ?? "");
  const result = await addRssFeed(getSqlClient(process.env), url);
  revalidatePath(ADMIN_PATH);

  const params = new URLSearchParams();
  if (result.status === "added") {
    params.set("added", "ok");
    params.set("url", result.feed.feedUrl);
  } else if (result.status === "exists") {
    params.set("added", "exists");
    params.set("url", result.feed.feedUrl);
  } else {
    params.set("added", "invalid");
    params.set("reason", result.reason);
    if (url.trim()) params.set("url", url.trim());
  }
  redirect(`${ADMIN_PATH}?${params.toString()}`);
}

export async function deleteFeedAction(formData: FormData): Promise<void> {
  const feedId = parseFeedId(formData.get("feed_id"));
  await softDeleteRssFeed(getSqlClient(process.env), feedId);
  revalidatePath(ADMIN_PATH);
}

export async function restoreFeedAction(formData: FormData): Promise<void> {
  const feedId = parseFeedId(formData.get("feed_id"));
  await restoreRssFeed(getSqlClient(process.env), feedId);
  revalidatePath(ADMIN_PATH);
}
