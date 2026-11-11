import { Button } from "@/components/ui/button";
import { sourceLabels } from "@/lib/labels";
import type { Batch, BatchItem, WorkNeighbor } from "@/lib/types";

export function BatchProgress({
  batch,
  order,
  cancel,
  stopping,
  offline,
}: {
  batch: Batch;
  order: WorkNeighbor[];
  cancel: () => void;
  stopping: boolean;
  offline: boolean;
}) {
  const busy = batch.status === "running";
  const done = batch.items.filter(
    (i) => !["pending", "running"].includes(i.status),
  ).length;
  const mixed = new Set(
    batch.items.map((item) => `${item.action}:${item.source ?? ""}`),
  ).size > 1;
  const label = (item: BatchItem) => {
    const code =
      item.code ||
      order.find((work) => work.id === item.id)?.label ||
      item.id;
    return [
      code,
      mixed && item.action === "force" ? "替换" : "",
      mixed && item.source ? sourceLabels[item.source] : "",
    ]
      .filter(Boolean)
      .join(" · ");
  };
  const current = batch.items.filter((i) => i.status === "running");
  const waiting = batch.items.filter((i) => i.status === "pending");
  const details = batch.items.filter(
    (i) => i.error || i.warnings?.length || i.note,
  );
  const cancelled = batch.items.filter((i) => i.status === "cancelled").length;
  const heading = busy
    ? `处理中 · ${done}/${batch.items.length}`
    : batch.status === "cancelled"
      ? "已取消"
      : batch.status === "interrupted"
        ? "已中断"
        : batch.status === "failed" ||
            batch.items.some((i) => i.status === "failed")
          ? "已结束"
          : "已完成";
  return (
    <section aria-label="当前批次" className="space-y-2 rounded-lg border p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="tabular-nums text-muted-foreground">
          {heading}
          {` · ${batch.items.filter((i) => i.status === "success").length} 成功 · ${batch.items.filter((i) => i.status === "failed").length} 失败 · ${batch.items.filter((i) => i.status === "skipped").length} 跳过`}
          {cancelled > 0 && ` · ${cancelled} 取消`}
        </p>
        {busy && (
          <Button size="sm" variant="outline" onClick={cancel} disabled={stopping}>
            {stopping ? "正在停止…" : "取消"}
          </Button>
        )}
      </div>
      {stopping && batch.items.some((item) => item.status === "running" && item.writing) && (
        <p className="text-muted-foreground">当前写入完成后停止</p>
      )}
      {busy && (
        <progress
          className="h-1.5 w-full accent-primary"
          max={batch.items.length || 1}
          value={done}
          aria-label="批次进度"
        />
      )}
      {busy && offline && (
        <p role="status" className="text-muted-foreground">无法更新任务进度</p>
      )}
      {current.length > 0 && (
        <p className="line-clamp-2 wrap-anywhere text-muted-foreground">
          当前：{current.map(label).join(" ")}
        </p>
      )}
      {waiting.length > 0 && (
        <p className="line-clamp-2 wrap-anywhere text-muted-foreground">
          等待：{waiting.map(label).join(" ")}
        </p>
      )}
      {(batch.error || details.length > 0) && (
        <details>
          <summary className="w-fit cursor-pointer text-muted-foreground">
            详情
          </summary>
          {batch.error && (
            <p role="alert" className="mt-2 text-destructive">
              {batch.error}
            </p>
          )}
          <ul className="mt-2 max-h-48 space-y-2 overflow-auto wrap-anywhere">
            {details.map((item, index) => {
              const error =
                item.status === "cancelled"
                  ? item.error?.replace(/^已取消[，；]\s*/, "")
                  : item.error;

              return (
                <li key={`${item.id}-${index}`}>
                  {label(item)}：
                  {error && (
                    <span>
                      {item.status === "skipped"
                        ? "跳过"
                        : item.status === "cancelled"
                          ? "已取消"
                          : "失败"}{" "}
                      · {error}
                    </span>
                  )}
                  {item.note && <span>跳过 · {item.note}</span>}
                  {item.warnings?.map((message, warningIndex) => (
                    <p key={warningIndex} className="text-muted-foreground">
                      警告 · {message}
                    </p>
                  ))}
                </li>
              );
            })}
          </ul>
        </details>
      )}
    </section>
  );
}
