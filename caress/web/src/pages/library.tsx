import {
  useEffect,
  useState,
  type Dispatch,
  type SetStateAction,
  type ReactNode,
} from "react";
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
import { statusFilters, actions } from "@/lib/labels";
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
  progress,
  submitting,
}: {
  state: LibraryPreferences;
  setState: Dispatch<SetStateAction<LibraryPreferences>>;
  setOrder: (order: WorkNeighbor[]) => void;
  revision: number;
  configured: boolean;
  batch: Batch | null;
  start: (ids: string[], action: Action, source?: Source) => Promise<boolean>;
  run: (fn: () => Promise<unknown>) => void;
  open: (id: string) => void;
  refresh: () => void;
  progress: ReactNode;
  submitting: boolean;
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
    setSelected(new Set());
    if (!configured) {
      setTotal(0);
      setOrder([]);
      setLoading(false);
      return () => controller.abort();
    }
    setLoading(true);
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
  }, [configured, query, filter, page, pageSize, revision, setState, setOrder]);
  const pages = Math.max(1, Math.ceil(total / pageSize));
  const results = new Map(batch?.items.map((item) => [item.id, item]));
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
  const mediaLabel = (w: Work) =>
    [
      w.media.some((m) => m.subtitles) ? "字幕" : "",
      w.media.some((m) => m.uncensored) ? "无码破解" : "",
      w.media.some((m) => m.part) ? "分集" : "",
      w.media.length > 1 ? `${w.media.length} 个版本` : "",
    ]
      .filter(Boolean)
      .join(" · ");
  return (
    <div className="space-y-4">
      <div className="flex min-h-9 flex-wrap items-center gap-2">
        <p className="text-base tabular-nums">
          {total} 部作品
          {selected.size > 0 && (
            <span className="text-sm text-muted-foreground"> · 已选 {selected.size}</span>
          )}
        </p>
        <RefreshButton
          onClick={refresh}
          disabled={!configured}
          pending={loading}
          label="刷新作品列表"
        />
      </div>
      {!configured && <Alert>请先连接 Emby 并选择媒体库</Alert>}
      {progress}
      <div className="flex flex-wrap items-center gap-2">
        <Select
          label="刮削方式"
          value={action}
          onChange={(value) => setAction(value as Action)}
          options={actions}
        />
        <Button
          disabled={submitting || !selected.size}
          onClick={() => run(async () => {
            const ids = [...selected];
            if (await start(ids, action)) select(ids, false);
          })}
        >
          开始执行
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
        <div className="flex flex-wrap items-center gap-2 border-b p-3 sm:gap-3">
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
                ...statusFilters,
              }}
            />
          </div>
        </div>
        {!configured ? null : error ? (
          <Alert role="alert" className="m-4 w-auto">
            <p>{error}</p>
            <Button variant="outline" onClick={refresh}>
              重试
            </Button>
          </Alert>
        ) : loading ? (
          <Empty>正在加载…</Empty>
        ) : !rows.length ? (
          <Empty>没有符合条件的作品</Empty>
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
              {rows.map((w) => {
                const actors = w.people
                  .filter((p) => p.type === "Actor")
                  .map((p) => p.name)
                  .join("、");
                return (
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
                        {w.code || w.media[0]?.filename || "无法识别番号"}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="block truncate" title={w.title}>
                        {w.title}
                      </span>
                    </TableCell>
                    <TableCell>
                      <span className="block truncate" title={actors}>
                        {actors}
                      </span>
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground">
                      {mediaLabel(w)}
                    </TableCell>
                    <TableCell>
                      <Status
                        status={
                          batch?.current.includes(w.id)
                            ? "running"
                            : results.get(w.id)?.status === "success"
                              ? "success"
                              : results.get(w.id)?.status === "failed"
                                ? "failed"
                                : w.status
                        }
                        title={w.error || undefined}
                      />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
        <Pagination
          page={Math.min(page, pages)}
          pages={pages}
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
