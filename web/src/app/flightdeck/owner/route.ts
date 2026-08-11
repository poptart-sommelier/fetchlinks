import { NextResponse, type NextRequest } from "next/server";

import {
  OWNER_COOKIE_NAME,
  OWNER_SESSION_MAX_AGE_SECONDS,
  createOwnerToken,
  safeReturnPath,
} from "../../../server/owner";

/**
 * The way into owner mode. It sits under `/flightdeck` so the existing Basic
 * challenge guards it; reaching this code at all means the credentials were
 * accepted. All it does is exchange that for a curation cookie and send the
 * reader back to the page they were on, filters and page number intact.
 */
export async function GET(request: NextRequest) {
  const token = await createOwnerToken();

  if (!token) {
    return new NextResponse(
      "Owner mode is not configured. Set FETCHLINKS_ADMIN_USER and FETCHLINKS_ADMIN_PASS.",
      { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } },
    );
  }

  const next = safeReturnPath(request.nextUrl.searchParams.get("next"));
  const response = NextResponse.redirect(new URL(next, request.nextUrl.origin));

  response.cookies.set({
    name: OWNER_COOKIE_NAME,
    value: token,
    httpOnly: true,
    // Lax rather than Strict: the cookie has to survive this very redirect,
    // and Strict would withhold it on navigations that arrive from elsewhere.
    sameSite: "lax",
    secure: true,
    path: "/",
    maxAge: OWNER_SESSION_MAX_AGE_SECONDS,
  });

  return response;
}
