export type Source =
  | "dmm"
  | "javdb"
  | "javbus"
  | "fc2"
  | "avsox"
  | "airav"
  | "1pondo"
  | "caribbeancom"
  | "heyzo"
  | "avbase";
export const sources: Source[] = [
  "dmm",
  "javdb",
  "javbus",
  "fc2",
  "avsox",
  "airav",
  "1pondo",
  "caribbeancom",
  "heyzo",
  "avbase",
];
export interface Work {
  needs_enrichment?: boolean;
  id: string;
  code: string | null;
  title: string;
  original_title: string;
  plot: string;
  year: number | null;
  release_date: string;
  studios: string[];
  genres: string[];
  tags: string[];
  people: { id?: string; name: string; type: string; image: string | null }[];
  poster: string | null;
  backdrops: string[];
  media: {
    path: string;
    filename: string;
    subtitles: boolean;
    uncensored: boolean;
    part: number | null;
  }[];
  status:
    | "pending"
    | "success"
    | "failed"
    | "rescrape_failed"
    | "cancelled"
    | "unsupported";
  error: string | null;
  last_result: string | null;
  source: Source | null;
  succeeded_at: string | null;
  emby_url: string;
  related?: Work[];
  related_errors?: string[];
}
export interface WorkNeighbor {
  id: string;
  label: string;
  position: number;
}
export interface LibraryPreferences {
  query: string;
  filter: string;
  page: number;
  pageSize: number;
}
export type Action = "scrape" | "force";
export type BatchStatus = "running" | "finished" | "failed" | "cancelled" | "interrupted";
export type BatchItemStatus = "pending" | "running" | "success" | "failed" | "skipped" | "cancelled";
export interface BatchItem {
  id: string;
  code: string | null;
  status: BatchItemStatus;
  action: Action;
  source: Source | null;
  error?: string | null;
  warnings?: string[];
  note?: string;
  writing?: boolean;
}
export interface Batch {
  id: string;
  status: BatchStatus;
  error?: string | null;
  items: BatchItem[];
  current: string[];
}
export interface Settings {
  configured: boolean;
  emby: {
    url: string;
    api_key?: string;
    has_api_key: boolean;
    library_ids: string[];
  };
  translation: {
    endpoint: string;
    model: string;
    api_key?: string;
    has_api_key: boolean;
  };
  routes: Record<string, Source[]>;
}
export type Box = [number, number, number, number];
export interface Crop {
  token: string;
  box: Box;
  width: number;
  height: number;
  preview: string;
}
