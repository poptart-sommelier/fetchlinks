"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { OWNER_COOKIE_NAME, safeReturnPath } from "../server/owner";

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
