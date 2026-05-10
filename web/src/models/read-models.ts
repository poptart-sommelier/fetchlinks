export type DatabaseId = number;

export type IsoDateString = string;

export type PostUrl = {
  id: DatabaseId;
  postId: DatabaseId;
  position: number;
  originalUrl: string;
  urlHash: string;
  unshortenedUrl: string | null;
  href: string;
};

export type PostSummary = {
  id: DatabaseId;
  source: string;
  author: string | null;
  description: string | null;
  directLink: string | null;
  dateCreated: IsoDateString;
  uniqueId: string;
  urls: PostUrl[];
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

export type SourceSummary = {
  source: string;
  postCount: number;
  latestPostDate: IsoDateString | null;
};

export type DomainSummary = {
  domain: string;
  postCount: number;
  urlCount: number;
  latestPostDate: IsoDateString | null;
};