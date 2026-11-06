import type { WorkDetail } from "./types";
import { errorText } from "./messages";
import { statuses } from "@/lib/labels";

const phases: Record<string, string> = {
  request: "请求数据",
  response: "收到响应",
  challenge: "访问验证",
  retry: "等待重试",
};
const outcomes: Record<string, string> = {
  unsupported: "不支持此番号类型",
};

const idle = {
  status: "missing",
  phase: "",
  message: "",
  elapsed: undefined,
  label: undefined,
};

export function sourceProgress(
  detail: WorkDetail,
  source: string,
  now = Date.now(),
) {
  const process = detail.process;
  if (process?.type === "update_portraits" || process?.type === "remap_actors") return idle;
  const active = process?.status === "running" || process?.status === "pending";
  const current = detail.variants?.find((item) => item.source === source);
  const stored = process?.result?.sources?.find(
    (item) => item.source === source,
  );
  const fresh =
    current?.fetched_at &&
    process?.created_at &&
    Date.parse(current.fetched_at) >= Date.parse(process.created_at) &&
    (active ||
      (!!process.finished_at &&
        Date.parse(current.fetched_at) <= Date.parse(process.finished_at)));
  const report =
    stored?.fetched_at &&
    process?.created_at &&
    (Date.parse(stored.fetched_at) < Date.parse(process.created_at) ||
      (!!process.finished_at &&
        Date.parse(stored.fetched_at) > Date.parse(process.finished_at)))
      ? undefined
      : stored;
  const result = process
    ? active
      ? fresh
        ? current
        : undefined
      : (report ?? (fresh ? current : undefined))
    : current;
  const events =
    process?.events?.filter((event) => event.source === source) ?? [];
  const started = events.find(
    (item) => item.stage === "source" && item.status === "running",
  );
  // Image download events do not prove that metadata was fetched in this task.
  const finished = events.findLast(
    (item) =>
      item.stage === "source" && !["running", "pending"].includes(item.status),
  );
  const event =
    finished ??
    (started
      ? events.at(-1)
      : events.findLast((item) => item.stage === "wait"));
  const status =
    result?.status ??
    finished?.status ??
    (active && event
      ? event.stage === "wait"
        ? "pending"
        : event.status
      : started && process?.status === "cancelled"
        ? "cancelled"
        : "missing");
  if (!statuses[status]) return idle;
  let elapsed =
    result?.elapsed_seconds ??
    (started && (finished || (active && !result))
      ? ((finished ? Date.parse(finished.time) : now) -
          Date.parse(started.time)) /
        1000
      : undefined);
  let phase = "";
  let message = "";
  let label: string | undefined;
  if (status === "found") {
    const metadata = result === current ? current?.metadata : undefined;
    const artwork = metadata?.artwork;
    const cover = artwork
      ? artwork.some((item) => item.role !== "sample")
      : result?.has_cover;
    const samples = artwork
      ? artwork.filter((item) => item.role === "sample").length
      : result?.sample_count;
    const plot =
      metadata && (typeof metadata.plot === "string" || metadata.plot === null)
        ? !!metadata.plot
        : result?.has_plot;
    const facts = [
      typeof cover === "boolean" ? (cover ? "有封面" : "无封面") : "",
      typeof samples === "number" && Number.isInteger(samples) && samples >= 0
        ? `${samples} 张剧照`
        : "",
      typeof plot === "boolean" ? (plot ? "有剧情" : "无剧情") : "",
    ].filter(Boolean);
    message = facts.join(" · ") || "未保留来源详情";
  } else if (status === "pending" || status === "running") {
    if (event?.stage === "wait") {
      const cooldown = event.message?.match(/^冷却 (\d+) 秒$/);
      if (cooldown) {
        const remaining = Math.max(
          0,
          Number(cooldown[1]) -
            Math.floor((now - Date.parse(event.time)) / 1000),
        );
        label = remaining > 0 ? "冷却中" : "等待中";
        message = remaining > 0 ? `冷却 ${remaining} 秒` : "等待来源可用";
      } else {
        label = "等待中";
        message = event.message || "";
      }
    } else {
      label = "刮削中";
      phase =
        event?.stage === "source"
          ? event.message === "打开网页"
            ? "改用网页"
            : event.message || ""
          : (phases[event?.stage ?? ""] ?? "");
      if (event?.stage === "retry") message = "网络请求失败";
    }
  } else if (status !== "missing") {
    message = result?.message
      ? errorText(result.message)
      : outcomes[status] || "";
  } else if (started && !active) {
    message = "未记录来源结果";
  }
  if (
    elapsed === undefined ||
    !Number.isFinite(elapsed) ||
    elapsed < 0 ||
    status === "missing"
  )
    elapsed = undefined;
  return { status, phase, message, elapsed, label };
}
