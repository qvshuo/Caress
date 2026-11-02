import { useDeferredValue, useMemo, useState } from "react";
import { ScanLine } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, valid } from "@/lib/api";
import type { Work } from "@/lib/types";
import {
  Empty,
  useSelection,
  ClearSelection,
  SummaryHeader,
  Pagination,
  Select,
  useListState,
  type ViewMode,
} from "./shared";
import { WorkFilters, WorkList, matchesWorkFilter } from "./work-list";

export function Library({
  works,
  configured,
  refresh,
  tasksChanged,
  run,
  navigate,
}: {
  works: Work[];
  configured: boolean;
  refresh: () => Promise<void>;
  tasksChanged: () => Promise<void>;
  run: (fn: () => Promise<void>) => void;
  navigate: (path: string) => void;
}) {
  const [state, setState] = useListState("library", {
    query: "",
    filter: "all",
    page: 1,
    pageSize: 100,
    view: "list" as ViewMode,
  });
  const { query, filter, page, pageSize, view } = state;
  const search = useDeferredValue(query.toLowerCase());
  const selection = useSelection();
  const { selected } = selection;
  const [mode, setMode] = useState("fill_missing");
  const [pending, setPending] = useState(false);
  const [scanned, setScanned] = useState(false);
  const rows = useMemo(
    () =>
      works.filter(
        (work) =>
          `${work.code} ${work.path} ${work.title ?? ""} ${work.actors?.join(" ") ?? ""}`
            .toLowerCase()
            .includes(search) && matchesWorkFilter(work.status, filter),
      ),
    [works, search, filter],
  );
  const pages = Math.max(1, Math.ceil(rows.length / pageSize)),
    current = Math.min(page, pages);
  const chosen = works.filter((work) => selected.has(work.path) && valid(work));
  function perform(fn: () => Promise<void>) {
    run(async () => {
      setPending(true);
      try {
        await fn();
      } finally {
        setPending(false);
      }
    });
  }
  return (
    <div className="space-y-4">
      <SummaryHeader
        count={works.length}
        unit="部作品"
        suffix={chosen.length ? ` · 已选 ${chosen.length}` : ""}
      />
      <div className="flex flex-wrap items-center gap-2">
        <Select
          label="刮削模式"
          value={mode}
          onChange={setMode}
          disabled={pending}
          options={{ fill_missing: "补齐缺失", refresh: "重新刮削" }}
        />
        <Button
          disabled={pending || !chosen.length}
          onClick={() =>
            perform(async () => {
              await api("tasks", "POST", {
                type: mode,
                codes: chosen.map((work) => work.code),
              });
              await tasksChanged();
              navigate("tasks");
            })
          }
        >
          刮削元数据
        </Button>
        <div className="ml-auto flex items-center gap-3">
          <span role="status" className="text-sm text-muted-foreground">
            {pending ? "正在处理，请稍候" : scanned ? "扫描完成" : ""}
          </span>
          <Button
            variant="outline"
            disabled={pending || !configured}
            onClick={() =>
              perform(async () => {
                setScanned(false);
                await api("scan", "POST");
                await refresh();
                setScanned(true);
              })
            }
          >
            <ScanLine />
            扫描作品
          </Button>
        </div>
      </div>
      <div className="overflow-hidden rounded-lg border">
        <WorkFilters
          query={query}
          filter={filter}
          view={view}
          onQuery={(query) => setState((s) => ({ ...s, query, page: 1 }))}
          onFilter={(filter) => setState((s) => ({ ...s, filter, page: 1 }))}
          onView={(view) => setState((s) => ({ ...s, view }))}
        >
          <ClearSelection selection={selection} />
        </WorkFilters>
        <WorkList
          works={rows.slice((current - 1) * pageSize, current * pageSize)}
          view={view}
          selection={selection}
          open={(work) =>
            navigate(`library/work/${encodeURIComponent(work.path)}`)
          }
        />
        {!rows.length && (
          <Empty>
            {query || filter !== "all"
              ? "没有匹配的作品"
              : configured
                ? "暂无作品，点击扫描作品"
                : "暂无作品"}
          </Empty>
        )}
        <Pagination
          page={current}
          pages={pages}
          total={rows.length}
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
