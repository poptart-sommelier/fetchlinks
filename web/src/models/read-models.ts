export type DatabaseId = number;

export type IsoDateString = string;

export type SourceType = "rss" | "reddit" | "bluesky" | "mastodon";

export type PostUrl = {
  id: DatabaseId;
  postId: DatabaseId;
  position: number;
  originalUrl: string;
  urlHash: string;
  unshortenedUrl: string | null;
  href: string;
  /** Normalized effective host, derived in the database. Empty when the URL
   * could not be parsed as one. */
  urlHost: string;
};

/** One arrival of a post: where it appeared and who posted it. A link found in
 * a feed and then on Reddit has two. */
export type PostOccurrence = {
  id: DatabaseId;
  postId: DatabaseId;
  sourceType: SourceType | null;
  channelKey: string;
  channelLabel: string;
  actorKey: string;
  actorLabel: string;
  source: string;
  directLink: string;
};

export type PostSummary = {
  id: DatabaseId;
  source: string;
  sourceType: SourceType | null;
  author: string | null;
  description: string | null;
  directLink: string | null;
  dateCreated: IsoDateString;
  uniqueId: string;
  urls: PostUrl[];
  occurrences: PostOccurrence[];
};

export type PostPage = {
  posts: PostSummary[];
  page: number;
  pageSize: number;
  totalPosts: number;
  totalPages: number;
  hasPreviousPage: boolean;
  hasNextPage: boolean;
};