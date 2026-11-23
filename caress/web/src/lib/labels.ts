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
  "": "全部作品",
  unsupported: "无法处理",
  failed: "处理失败",
  success: "处理完成",
  pending: "尚未处理",
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
