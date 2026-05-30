export type MastodonFollow = {
  instanceName: string;
  accountId: string;
  acct: string;
  displayName: string | null;
  url: string | null;
  syncedAt: string;
  latestPostAt: string | null;
  postSource: string | null;
};

export type MastodonFollowsSnapshot = {
  follows: MastodonFollow[];
  lastSyncedAt: string | null;
};
