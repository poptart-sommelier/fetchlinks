import type { PostSummary } from "../models/read-models";
import { composeTargetKey, type CurationTarget } from "./curation";

/**
 * What the owner may manage from one article.
 *
 * Derived from the post rather than accepted from the browser. Every action
 * rebuilds this list server-side, so a hand-crafted form cannot invent a target
 * or attach an attacker-chosen label to a real one.
 *
 * Empty keys are skipped. They mean that dimension does not exist for the
 * source -- RSS feeds have no actor -- or that the origin predates identity
 * capture. Neither is something the owner can act on reliably.
 */
export function curationTargetsFor(post: PostSummary): CurationTarget[] {
  const targets: CurationTarget[] = [
    {
      type: "post",
      key: post.uniqueId,
      label: post.description?.trim() || post.source || post.uniqueId,
    },
  ];

  for (const occurrence of post.occurrences) {
    const sourceType = occurrence.sourceType ?? "";

    if (occurrence.channelKey) {
      targets.push({
        type: "channel",
        key: composeTargetKey(sourceType, occurrence.channelKey),
        label: occurrence.channelLabel || occurrence.channelKey,
      });
    }

    if (occurrence.actorKey) {
      targets.push({
        type: "actor",
        key: composeTargetKey(sourceType, occurrence.actorKey),
        label: occurrence.actorLabel || occurrence.actorKey,
      });
    }
  }

  for (const url of post.urls) {
    if (url.urlHost) {
      targets.push({ type: "domain", key: url.urlHost, label: url.urlHost });
    }
  }

  return dedupe(targets);
}

/**
 * The same domain often appears on several URLs, and one origin can be recorded
 * more than once. One card must never offer two controls for one decision.
 */
function dedupe(targets: readonly CurationTarget[]): CurationTarget[] {
  const seen = new Set<string>();

  return targets.filter((target) => {
    const identity = `${target.type}\u001f${target.key}`;

    if (seen.has(identity)) {
      return false;
    }

    seen.add(identity);
    return true;
  });
}
