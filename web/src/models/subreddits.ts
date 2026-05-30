export type SubredditStatus = "active" | "disabled" | "removed";

export type Subreddit = {
  id: number;
  name: string;
  normalizedName: string;
  enabled: boolean;
  addedAt: string;
  deletedAt: string | null;
  lastFetchedAt: string | null;
  latestPostAt: string | null;
  postSource: string | null;
  status: SubredditStatus;
};

export type SubredditListFilters = {
  status?: SubredditStatus | "all";
  q?: string;
};
