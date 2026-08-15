import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import StatusGuidePage from "./page";

describe("the status guide", () => {
  const html = renderToStaticMarkup(<StatusGuidePage />);

  it("gets back to the status page", () => {
    expect(html).toContain("/flightdeck/status");
    expect(html).toContain("How to read System status");
  });

  // Two vocabularies overlap on this page and are easy to confuse: whether a
  // job is reporting, and how its last run went.
  it("keeps health and outcome apart", () => {
    expect(html).toContain("What the health labels mean");
    expect(html).toContain("What a run\u2019s outcome means");
  });

  it("defines every outcome word the status page can print", () => {
    for (const word of ["ok", "partial", "failed", "running", "skipped"]) {
      expect(html).toContain(`<strong>${word}</strong>`);
    }
  });

  it("says plainly that partial is usually normal", () => {
    expect(html).toContain("partial is the normal state of the world");
  });

  // The whole reason this page exists rather than a document in the repo.
  it("carries the steps for a Pi that has stopped reporting", () => {
    expect(html).toContain("If the Publisher has not reported");
    expect(html).toContain("journalctl");
    expect(html).toContain("./deploy/bootstrap.sh");
  });
});
