export const statuses = {
  pending: "待处理",
  success: "已完成",
  failed: "失败",
  rescrape_failed: "失败",
  cancelled: "待处理",
  unsupported: "不可处理",
  running: "处理中",
};
export const statusFilters = {
  pending: statuses.pending,
  success: statuses.success,
  failed: statuses.failed,
  unsupported: statuses.unsupported,
};
export const actions = {
  scrape: "刮削缺少的元数据",
  force: "替换所有的元数据",
};
