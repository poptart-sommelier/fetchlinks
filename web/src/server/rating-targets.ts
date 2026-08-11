import type { PostSummary } from "../models/read-models";
import { composeTargetKey, type RatingTarget } from "./ratings";

/**
 * What the owner may rate about one post.
 *
 * Derived from the post rather than accepted from the browser. The rating
 * action rebuilds this list server-side and refuses anything not in it, so a
 * hand-crafted form cannot invent a target or attach a label of its choosing
 * to someone else's.
 *
 * Targets with an empty key are skipped. An empty key means the dimension does
 * not exist for that source -- RSS feeds have no author -- or that the origin
 * predates identity capture. Neither is a thing anyone can meaningfully judge.
 */
export function ratingTargetsFor(post: PostSummary): RatingTarget[] {
  const targets: RatingTarget[] = [
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
 * The same domain usually appears on several of a post's URLs, and a link that
 * arrived twice from one subreddit shares its channel. Rating it twice on one
 * card would be two controls for one opinion.
 */
function dedupe(targets: readonly RatingTarget[]): RatingTarget[] {
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
