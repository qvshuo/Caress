export interface Work {
  progress_at?: string | null;
  title?: string | null;
  actors?: string[];
  poster?: string | null;
  code: string;
  available?: boolean;
  category: string;
  status: string;
  media?: { id: string; path: string; filename: string; subtitles: boolean; uncensored: boolean; part: number | null; sync?: { status?: string; error?: string; item_id?: string } }[];
  warnings: string[];
}
export interface Actor {
  name: string;
}
export interface ActorMapping {
  original_name: string;
  name: string;
  actor_id: string | null;
  type: number | null;
}
export interface ActorLibrarySnapshot {
  scope: string | null;
  query: string;
  rows: LibraryActor[];
}
export interface FieldInputs {
  original_fields: Record<string, string | Actor[] | null>;
  actor_mappings: ActorMapping[];
  title_text: string | null;
  mapping_fingerprint: string;
}
export interface WorkMetadata {
  code: string;
  title: string;
  original_title?: string | null;
  plot?: string | null;
  release_date?: string | null;
  year?: number | null;
  studio?: string | null;
  directors?: string[];
  actors?: Actor[];
  genres?: string[];
  tags?: string[];
}
export interface SourceMetadata extends WorkMetadata {
  artwork: { url: string; role: string; source: string; referer: string }[];
}
export interface SourceReport {
  elapsed_seconds?: number | null;
  fetched_at?: string | null;
  source: string;
  status: string;
  message?: string | null;
  has_plot?: boolean;
  has_cover?: boolean;
  sample_count?: number;
}
export interface Report {
  actor_updates?: { from: string; to: string; actor_id: string | null }[];
  error?: string;
  warnings?: string[];
  sources?: SourceReport[];
  partial?: boolean;
  stages?: Record<string, string>;
  incomplete_stages?: string[];
  translation_errors?: { field: string; error: string }[];
  actor_issues?: { name: string; candidates?: string[] }[];
  images?: { status: string; message?: string; error?: string }[];
  portraits?: { status: string; name: string; error?: string }[];
}
export interface ProgressEvent {
  time: string;
  stage: string;
  source?: string | null;
  message?: string | null;
  status: string;
}
export interface Variant extends SourceReport, Partial<FieldInputs> {
  translation_errors?: { field: string; error: string }[];
  metadata: SourceMetadata | null;
  translated_fields?: string[];
  processed_fields?: Partial<WorkMetadata>;
}
export interface Selection extends Partial<FieldInputs> {
  field_sources?: Record<string, string>;
  artwork?: { roles: Record<string, { file?: string; choice?: ImageChoice; protected?: boolean; origin?: string; status: string }>; samples_protected: boolean };
  translated_fields?: string[];
  manual_fields?: string[];
}
export interface WorkDetail extends Work {
  process?: TaskItem & {
    task_id: string;
    type?: string;
    sequence?: number;
    created_at?: string;
    finished_at?: string | null;
  };
  route_sources?: string[];
  variants?: Variant[];
  selection?: Selection;
  editable: boolean;
  metadata: WorkMetadata | null;
  artwork: string[];
  artwork_versions: Record<string, string>;
  last_result: Report | null;
  health?: { metadata: boolean; incomplete_stages: string[]; artwork_problems: string[] } | null;
}
export interface TaskItem {
  code: string;
  status: string;
  stage?: ProgressEvent;
  events?: ProgressEvent[];
  result?: Report | null;
}
export interface Task {
  id: string;
  sequence: number;
  created_at: string;
  type: string;
  status: string;
  total: number;
  progress?: number;
  success: number;
  failed: number;
  skipped: number;
  partial?: number;
  error?: string | null;
  params?: { codes?: string[]; actor_names?: string[] };
}
export interface TaskDetail extends Task {
  items: TaskItem[];
  page: number;
  pages: number;
  matched: number;
}
export interface Site {
  base_url: string | null;
  interval_seconds: number | null;
  cookie?: string;
}
export interface Translation {
  endpoint: string;
  model: string;
  api_key: string;
  timeout_seconds: number;
  interval_seconds: number;
}
export interface Settings {
  pending_settings: boolean;
  translation_status: { valid: boolean | null; message: string };
  default_routes: Record<string, string[]>;
  media_directories: string[];
  flaresolverr_url: string | null;
  timeout_seconds: number;
  retries: number;
  retry_delay_seconds: number;
  source_timeout_seconds: number;
  sites: Record<string, Site>;
  routes: Record<string, string[]>;
  emby: { url: string; api_key: string };
  translation: Translation;
  configured: boolean;
  source_defaults: Record<string, string>;
  source_intervals: Record<string, number>;
}
export interface DirectoryView {
  path: string | null;
  parent: string | null;
  directories: { name: string; path: string }[];
}
export type Box = [number, number, number, number];
export interface Crop {
  box: Box;
  faces: unknown[];
  width: number;
  height: number;
  fingerprint: string;
  preview: string;
}

export interface LibraryActor {
  name: string;
  actors: string[];
  codes: string[];
  count: number;
  portrait: boolean;
  portrait_name: string;
  portrait_version?: string;
  portrait_status: string;
  portrait_eligible: boolean;
  status: string;
}
export interface ImageChoice {
  source: string;
  index?: number;
  file?: string;
  box?: Box;
  fingerprint?: string;
}
