import type { Source } from "./types";

export const statuses = {
  pending: "待处理",
  success: "已处理",
  failed: "失败",
  rescrape_failed: "失败",
  cancelled: "待处理",
  unsupported: "不支持",
  running: "处理中",
};
export const statusFilters = {
  unsupported: statuses.unsupported,
  failed: statuses.failed,
  success: statuses.success,
  pending: statuses.pending,
};
export const actions = {
  scrape: "刮削缺少的元数据",
  force: "替换所有的元数据",
};
export const sourceLabels: Record<Source, string> = {
  dmm: "DMM",
  javdb: "JavDB",
  javbus: "JavBus",
  fc2: "FC2",
  avsox: "AVSOX",
  airav: "AirAV",
  "1pondo": "一本道",
  caribbeancom: "Caribbeancom",
  heyzo: "HEYZO",
  avbase: "AVbase",
};
