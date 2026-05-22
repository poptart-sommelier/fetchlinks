export type RssFeedStatus = "active" | "disabled" | "removed";

export type RssFeed = {
  id: number;
  feedUrl: string;
  normalizedUrl: string;
  enabled: boolean;
  addedAt: string;
  deletedAt: string | null;
  lastFetchedAt: string | null;
  lastSuccessAt: string | null;
  lastStatus: number | null;
  lastError: string | null;
  consecutiveFailures: number;
  etag: string | null;
  lastModified: string | null;
  latestEntryAt: string | null;
  status: RssFeedStatus;
};

export type RssFeedListFilters = {
  status?: RssFeedStatus | "all";
  q?: string;
};
