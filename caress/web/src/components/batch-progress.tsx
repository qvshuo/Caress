import { Button } from "@/components/ui/button";
import type { Batch, WorkNeighbor } from "@/lib/types";

export function BatchProgress({
  batch,
  order,
  cancel,
}: {
  batch: Batch;
  order: WorkNeighbor[];
  cancel: () => void;
}) {
  const busy = batch.status === "running";
  const done = batch.items.filter(
    (i) => !["pending", "running"].includes(i.status),
  ).length;
  const label = (item: Batch["items"][number]) =>
    item.code || order.find((w) => w.id === item.id)?.label || item.id;
  const current = batch.items.filter((i) => i.status === "running");
  const waiting = batch.items.filter((i) => i.status === "pending");
  const details = batch.items.filter(
    (i) => i.error || i.warnings?.length || i.note,
  );
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
        </p>
        {busy && (
          <Button size="sm" variant="outline" onClick={cancel}>
            取消
          </Button>
        )}
      </div>
      {busy && (
        <progress
          className="h-1.5 w-full accent-primary"
          max={batch.items.length || 1}
          value={done}
          aria-label="批次进度"
        />
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
            {details.map((item, index) => (
              <li key={`${item.id}-${index}`}>
                {label(item)}：
                {item.error && (
                  <span>
                    {item.status === "skipped"
                      ? "跳过"
                      : item.status === "cancelled"
                        ? "取消"
                        : "失败"}{" "}
                    · {item.error}
                  </span>
                )}
                {item.note && <span>跳过 · {item.note}</span>}
                {item.warnings?.map((message, index) => (
                  <p key={index} className="text-muted-foreground">
                    警告 · {message}
                  </p>
                ))}
              </li>
            ))}
          </ul>
        </details>
      )}
    </section>
  );
}
