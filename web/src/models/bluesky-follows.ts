export type BlueskyFollow = {
  did: string;
  handle: string;
  displayName: string | null;
  syncedAt: string;
  latestPostAt: string | null;
  postSource: string | null;
};

export type BlueskyFollowsSnapshot = {
  follows: BlueskyFollow[];
  lastSyncedAt: string | null;
};
