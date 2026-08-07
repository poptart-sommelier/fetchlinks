import { NextResponse, type NextRequest } from "next/server";

export const config = {
  matcher: ["/flightdeck/:path*"],
};

const REALM = "Fetchlinks admin";

export function proxy(request: NextRequest): NextResponse {
  const expectedUser = process.env.FETCHLINKS_ADMIN_USER?.trim();
  const expectedPass = process.env.FETCHLINKS_ADMIN_PASS;

  if (!expectedUser || !expectedPass) {
    return new NextResponse(
      "Admin UI is not configured. Set FETCHLINKS_ADMIN_USER and FETCHLINKS_ADMIN_PASS.",
      { status: 503, headers: { "Content-Type": "text/plain; charset=utf-8" } },
    );
  }

  const header = request.headers.get("authorization") ?? "";
  if (!header.toLowerCase().startsWith("basic ")) {
    return unauthorized();
  }

  let decoded: string;
  try {
    decoded = atob(header.slice("basic ".length).trim());
  } catch {
    return unauthorized();
  }

  const separator = decoded.indexOf(":");
  if (separator < 0) {
    return unauthorized();
  }

  const providedUser = decoded.slice(0, separator);
  const providedPass = decoded.slice(separator + 1);

  if (
    !safeEqual(providedUser, expectedUser) ||
    !safeEqual(providedPass, expectedPass)
  ) {
    return unauthorized();
  }

  return NextResponse.next();
}

function unauthorized(): NextResponse {
  return new NextResponse("Authentication required.", {
    status: 401,
    headers: {
      "WWW-Authenticate": `Basic realm="${REALM}", charset="UTF-8"`,
      "Content-Type": "text/plain; charset=utf-8",
    },
  });
}

function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) {
    return false;
  }
  let result = 0;
  for (let i = 0; i < a.length; i += 1) {
    result |= a.charCodeAt(i) ^ b.charCodeAt(i);
  }
  return result === 0;
}
