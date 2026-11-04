import { validationText } from "./messages";
export async function api<T>(
  path: string,
  method = "GET",
  data?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/${path}`, {
      method,
      signal,
      headers: data === undefined ? undefined : { "Content-Type": "application/json" },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error("无法连接服务，请检查网络。");
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error(response.ok ? "服务响应格式异常，请重试。" : `服务暂不可用（${response.status}）`);
  }
  if (!response.ok) {
    const detail = (value as { detail?: unknown } | null)?.detail;
    throw new Error(detail === undefined ? `服务请求失败（${response.status}）` : validationText(detail));
  }
  return value as T;
}
export const sources: Record<string, string> = {
  dmm: "DMM",
  javdb: "JavDB",
  javbus: "JavBus",
  fc2: "FC2 官方",
  avsox: "AVSOX",
  airav: "AirAV",
  "1pondo": "一本道",
  caribbeancom: "加勒比",
  heyzo: "HEYZO",
  avbase: "AVbase",
};
export const categories: Record<string, string> = {
  censored: "有码",
  uncensored: "无码",
  fc2: "FC2",
  other: "其他",
  unknown: "未分类",
};
export const statuses: Record<string, string> = {
  pending: "等待中",
  queued: "等待中",
  partial: "部分完成",
  missing: "尚未刮削",
  cooldown: "冷却中",
  rate_limited: "来源限流",
  timeout: "来源超时",
  running: "进行中",
  scraped: "已完成",
  failed: "刮削失败",
  success: "已完成",
  finished: "已结束",
  invalid_key: "名称异常",
  conflict: "番号重复",
  cancelled: "已取消",
  skipped: "已跳过",
  found: "已刮削",
  not_found: "未找到",
  login_required: "需要登录",
  region_blocked: "地区限制",
  ip_blocked: "访问受限",
  blocked: "访问受限",
  network_error: "网络错误",
  parse_error: "解析失败",
  identity_mismatch: "番号不符",
  unsupported: "不支持",
};
export const stages: Record<string, string> = {
  mapping: "映射演员",
  translation: "翻译文本",
  metadata: "保存元数据",
  images: "下载图片",
  portraits: "获取头像",
  crop: "裁剪封面",
};
export const active = (value: { status: string }) =>
  ["pending", "running"].includes(value.status);
export const valid = (value: { status: string }) =>
  !["invalid_key", "conflict"].includes(value.status);
export function formatDate(value: string, progress = false): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  const day = progress
    ? `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())}`
    : `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  return `${day} ${date.toLocaleTimeString("zh-CN", { hour12: false })}`;
}
