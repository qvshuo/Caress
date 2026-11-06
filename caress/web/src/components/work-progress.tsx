import { Alert, AlertTitle, AlertDescription } from "@/components/ui/alert";
import { errorText } from "@/lib/messages";
import { stages } from "@/lib/labels";
import type { ProgressEvent, Report } from "@/lib/types";

const portraitOutcomes: Record<string, string> = {
  saved: "头像已保存",
  error: "头像获取失败",
  not_found: "未找到头像",
};

export function Process({
  events,
  running,
}: {
  events: ProgressEvent[];
  running?: boolean;
}) {
  const event = events.findLast(
    (item) => item.stage in stages || item.stage === "finished",
  );
  if (!event || !running) return null;
  const label =
    event.stage === "portraits" && event.status !== "running"
      ? portraitOutcomes[event.status]
      : stages[event.stage];
  if (!label) return null;
  return <p className="text-sm text-muted-foreground">{label}</p>;
}

export function Diagnostics({
  report,
  title,
  active = false,
  taskRecord = false,
}: {
  report: Report | null | undefined;
  title: string;
  active?: boolean;
  taskRecord?: boolean;
}) {
  if (!report) return null;
  const labels: Record<string, string> = {
    metadata: "元数据",
    translation: "翻译",
    images: "图片",
    portraits: "头像",
    mapping: "演员映射",
  };
  const incomplete = (report.incomplete_stages ?? []).filter(
    (stage) => !active || report.stages?.[stage] !== "pending",
  );
  const notices = [
    report.error ? errorText(report.error) : "",
    ...(taskRecord ? (report.warnings ?? []) : [])
      .filter(
        (warning) =>
          !warning.includes("used webpage fallback") &&
          !warning.includes("missing plot") &&
          !warning.includes("missing artwork") &&
          !report.sources?.some(
            (source) =>
              source.status !== "found" &&
              warning.startsWith(`${source.source}:`),
          ),
      )
      .map((warning) => errorText(warning)),
    ...(report.translation_errors ?? []).map(
      (item) => `翻译：${errorText(item.error)}`,
    ),
    ...(report.images ?? [])
      .filter((item) => item.status === "error")
      .map((item) => `图片：${errorText(item.message ?? item.error)}`),
    ...(report.portraits ?? [])
      .filter((item) => item.status === "error")
      .map((item) => `头像：${item.name} · ${errorText(item.error)}`),
    incomplete.length
      ? `未完成：${incomplete.map((stage) => labels[stage] ?? stage).join("、")}`
      : report.partial && !active
        ? "部分阶段未完成"
        : "",
  ].filter(Boolean);
  const actorUpdates = taskRecord ? (report.actor_updates ?? []) : [];
  const missingPortraits = taskRecord
    ? (report.portraits ?? [])
        .filter((item) => item.status === "not_found")
        .map((item) => item.name)
    : [];
  const preservedNames = taskRecord
    ? (report.actor_issues ?? [])
        .filter(
          (issue) =>
            !(report.portraits ?? []).some(
              (portrait) =>
                portrait.name === issue.name && portrait.status === "error",
            ),
        )
        .map((issue) => issue.name)
    : [];
  if (!notices.length && !actorUpdates.length && !missingPortraits.length && !preservedNames.length)
    return null;
  return (
    <Alert
      role="status"
      className={
        notices.length ? "border-amber-500/20 bg-amber-500/5" : "bg-muted/20"
      }
    >
      <AlertTitle>{notices.length ? title : "处理结果"}</AlertTitle>
      <AlertDescription>
        {taskRecord && <p>记录当次结果，后续修复不改写</p>}
        {[...new Set(notices)].map((notice) => (
          <p key={notice}>{notice}</p>
        ))}
        {actorUpdates.map((item) => (
          <p key={item.from}>演员：{item.from} → {item.to}</p>
        ))}
        {missingPortraits.length > 0 && (
          <p>未匹配头像：{[...new Set(missingPortraits)].join("、")}</p>
        )}
        {preservedNames.length > 0 && (
          <p>
            别名有歧义，已保留署名：{[...new Set(preservedNames)].join("、")}
          </p>
        )}
      </AlertDescription>
    </Alert>
  );
}
