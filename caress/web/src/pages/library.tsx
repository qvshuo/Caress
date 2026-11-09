import { useEffect, useState, type Dispatch, type SetStateAction } from "react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Empty } from "@/components/ui/empty";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Select } from "@/components/form-controls";
import {
  Pagination,
  SearchInput,
  SelectionCheckbox,
  rowNavigation,
} from "@/components/list-controls";
import { api } from "@/lib/api";
import type {
  Action,
  Batch,
  Source,
  Work,
  LibraryPreferences,
  WorkNeighbor,
} from "@/lib/types";
import { statuses, actions } from "@/lib/labels";
import { RefreshButton } from "@/components/refresh-button";
import { Status } from "@/components/status";
export function Library({
  state,
  setState,
  setOrder,
  revision,
  configured,
  batch,
  start,
  run,
  open,
  refresh,
  cancel,
}: {
  state: LibraryPreferences;
  setState: Dispatch<SetStateAction<LibraryPreferences>>;
  setOrder: (order: WorkNeighbor[]) => void;
  revision: number;
  configured: boolean;
  batch: Batch | null;
  start: (ids: string[], action: Action, source?: Source) => Promise<void>;
  run: (fn: () => Promise<unknown>) => void;
  open: (id: string) => void;
  refresh: () => void;
  cancel: () => Promise<void>;
}) {
  const { query, filter, page, pageSize } = state;
  const [rows, setRows] = useState<Work[]>([]),
    [total, setTotal] = useState(0),
    [selected, setSelected] = useState<Set<string>>(new Set()),
    [loading, setLoading] = useState(true),
    [error, setError] = useState(""),
    [action, setAction] = useState<Action>("scrape");
  useEffect(() => {
    const controller = new AbortController();
    setRows([]);
    setError("");
    setLoading(true);
    setSelected(new Set());
    const timer = setTimeout(
      () => {
        void api<{ items: Work[]; total: number; order: WorkNeighbor[] }>(
          `works?${new URLSearchParams({ q: query, status: filter, offset: String((page - 1) * pageSize), limit: String(pageSize) })}`,
          "GET",
          undefined,
          controller.signal,
        )
          .then((result) => {
            if (controller.signal.aborted) return;
            setRows(result.items);
            setOrder(result.order || []);
            setTotal(result.total);
            const pages = Math.max(1, Math.ceil(result.total / pageSize));
            if (page > pages) setState((s) => ({ ...s, page: pages }));
          })
          .catch((e: Error) => {
            if (!controller.signal.aborted) setError(e.message);
          })
          .finally(() => {
            if (!controller.signal.aborted) setLoading(false);
          });
      },
      query ? 200 : 0,
    );
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, filter, page, pageSize, revision, setState, setOrder]);
  const busy = batch?.status === "running",
    pages = Math.max(1, Math.ceil(total / pageSize));
  function select(ids: string[], checked: boolean) {
    setSelected((old) => {
      const next = new Set(old);
      for (const id of ids) {
        if (checked) next.add(id);
        else next.delete(id);
      }
      return next;
    });
  }
  const usable = rows
    .filter((w) => w.status !== "unsupported")
    .map((w) => w.id);
  const checkbox = (w: Work) => (
    <SelectionCheckbox
      ids={w.status === "unsupported" ? [] : [w.id]}
      selected={selected}
      onChange={select}
      label={`选择 ${w.code || w.title}`}
    />
  );
  const selection = (
    <SelectionCheckbox
      ids={usable}
      selected={selected}
      onChange={select}
      label="选择本页"
    />
  );
  const done =
    batch?.items.filter((i) => !["pending", "running"].includes(i.status))
      .length ?? 0;
  const mediaLabel = (w: Work) =>
    [
      w.media.some((m) => m.subtitles) ? "字幕" : "",
      w.media.some((m) => m.uncensored) ? "破解" : "",
      w.media.some((m) => m.part) ? "分集" : "",
      w.media.length > 1 ? `${w.media.length} 个版本` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  return (
    <div className="space-y-4">
      <div className="flex min-h-9 flex-wrap items-center justify-between gap-3">
        <p className="text-base font-medium tabular-nums">
          {total} 部作品{selected.size ? ` · 已选 ${selected.size}` : ""}
        </p>
        <RefreshButton
          onClick={refresh}
          disabled={!configured}
          pending={loading}
          label="刷新作品列表"
        />
      </div>
      {!configured && <Alert>请先连接 Emby 并选择媒体库</Alert>}
      {batch && (
        <section
          aria-label="当前批次"
          className="space-y-2 rounded-lg border p-4 text-sm"
        >
          <div className="flex items-center justify-between gap-3">
            <p className="tabular-nums">
              {busy ? "正在处理" : "最近批次"} · {done}/{batch.items.length} ·
              成功 {batch.items.filter((i) => i.status === "success").length} ·
              失败 {batch.items.filter((i) => i.status === "failed").length} ·
              跳过 {batch.items.filter((i) => i.status === "skipped").length}
            </p>
            {busy && (
              <Button size="sm" variant="outline" onClick={() => run(cancel)}>
                取消
              </Button>
            )}
          </div>
          <progress
            className="h-2 w-full accent-primary"
            max={batch.items.length || 1}
            value={done}
            aria-label="批次进度"
          />
          {!!batch.current.length && (
            <p className="text-muted-foreground">
              当前：
              {batch.items
                .filter((i) => batch.current.includes(i.id))
                .map((i) => i.code || i.id)
                .join("、")}
            </p>
          )}
          {batch.error && (
            <p role="alert" className="text-destructive">
              {batch.error}
            </p>
          )}
          {batch.items.some((i) => i.error || i.warnings?.length) && (
            <details>
              <summary className="cursor-pointer">处理详情</summary>
              <ul className="mt-2 max-h-48 space-y-2 overflow-auto">
                {batch.items
                  .filter((i) => i.error || i.warnings?.length)
                  .map((i) => (
                    <li key={i.id}>
                      {i.code || i.id}：{i.error || i.warnings?.join("；")}
                    </li>
                  ))}
              </ul>
            </details>
          )}
        </section>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          label="刮削操作"
          value={action}
          onChange={(value) => setAction(value as Action)}
          options={actions}
        />
        <Button
          disabled={busy || !selected.size}
          onClick={() => run(() => start([...selected], action))}
        >
          开始
        </Button>
        {!!selected.size && (
          <Button
            variant="ghost"
            size="sm"
            onClick={() => setSelected(new Set())}
          >
            清除选择
          </Button>
        )}
      </div>
      <div className="overflow-hidden rounded-lg border">
        <div className="flex flex-wrap items-center gap-3 border-b p-4">
          <SearchInput
            value={query}
            onChange={(query) => setState((s) => ({ ...s, query, page: 1 }))}
          />
          <div className="ml-auto flex items-center gap-3">
            <Select
              label="筛选状态"
              value={filter}
              onChange={(filter) =>
                setState((s) => ({ ...s, filter, page: 1 }))
              }
              options={{
                "": "全部状态",
                ...Object.fromEntries(
                  Object.entries(statuses).filter(([key]) => key !== "running"),
                ),
              }}
            />
          </div>
        </div>
        {error ? (
          <Alert role="alert" className="m-4">
            {error}，请检查连接后刷新
          </Alert>
        ) : loading ? (
          <Empty>正在读取 Emby…</Empty>
        ) : !rows.length ? (
          <Empty>没有匹配的作品</Empty>
        ) : (
          <Table className="min-w-[760px]">
            <TableHeader>
              <TableRow>
                <TableHead className="w-12 pl-4">{selection}</TableHead>
                <TableHead className="w-36">番号</TableHead>
                <TableHead>标题</TableHead>
                <TableHead className="w-44">演员</TableHead>
                <TableHead className="w-24">版本</TableHead>
                <TableHead className="w-24">状态</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {rows.map((w) => (
                <TableRow
                  key={w.id}
                  aria-label={`查看 ${w.code || w.title}`}
                  {...rowNavigation(() => open(w.id))}
                >
                  <TableCell className="pl-4">{checkbox(w)}</TableCell>
                  <TableCell className="font-medium">
                    <span
                      className="block truncate"
                      title={w.code || w.media[0]?.filename}
                    >
                      {w.code || w.media[0]?.filename || "无法识别"}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span className="block truncate" title={w.title}>
                      {w.title}
                    </span>
                  </TableCell>
                  <TableCell>
                    <span
                      className="block truncate"
                      title={w.people
                        .filter((p) => p.type === "Actor")
                        .map((p) => p.name)
                        .join("、")}
                    >
                      {w.people
                        .filter((p) => p.type === "Actor")
                        .map((p) => p.name)
                        .join("、")}
                    </span>
                  </TableCell>
                  <TableCell className="text-xs text-muted-foreground">
                    {mediaLabel(w)}
                  </TableCell>
                  <TableCell>
                    <Status
                      status={
                        batch?.current.includes(w.id) ? "running" : w.status
                      }
                      title={w.error || undefined}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
        <Pagination
          page={Math.min(page, pages)}
          pages={pages}
          total={total}
          pageSize={pageSize}
          onChange={(page) => setState((s) => ({ ...s, page }))}
          onPageSizeChange={(pageSize) =>
            setState((s) => ({ ...s, pageSize, page: 1 }))
          }
        />
      </div>
    </div>
  );
}
