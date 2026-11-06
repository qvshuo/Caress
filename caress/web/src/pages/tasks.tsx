import { useEffect, useMemo, useState, type ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { errorText } from "@/lib/messages";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { api } from "@/lib/api";
import { active, formatDate } from "@/lib/utils";
import type {
  Task,
  TaskDetail,
  Work,
} from "@/lib/types";
import { Empty } from "@/components/ui/empty";
import {
  SearchInput,
  Pagination,
  rowNavigation,
  defaultListView,
} from "@/components/list-controls";
import { Select } from "@/components/form-controls";
import { Status } from "@/components/status";
import { useDebounce } from "@/hooks/use-debounce";
import { useListState } from "@/hooks/use-list-state";
import type { Confirm } from "@/components/confirmation-dialog";

import { WorkFilters, WorkList } from "@/components/work-list";

const taskTypes: Record<string, string> = {
  fill_missing: "补齐缺失",
  refresh: "重新刮削",
  update_portraits: "更新头像",
  remap_actors: "重新映射",
};

function TaskSummary({ task }: { task: Task }) {
  const counts = [
    `总计 ${task.total}`,
    `成功 ${task.success}`,
    `跳过 ${task.skipped}`,
    `失败 ${task.failed}`,
    ...(task.partial ? [`待补全 ${task.partial}`] : []),
  ];
  return (
    <span className="inline-flex flex-wrap gap-x-1 text-sm text-muted-foreground tabular-nums">
      {counts.map((count, index) => (
        <span key={count} className="whitespace-nowrap">
          {index > 0 && "· "}
          {count}
        </span>
      ))}
    </span>
  );
}

interface TaskProps {
  tick: number;
  tasks: Task[];
  id?: string;
  works: Work[];
  refresh: () => Promise<void>;
  run: (fn: () => Promise<void>) => void;
  navigate: (path: string) => void;
  confirm: Confirm;
}
export function Tasks({
  tick,
  tasks,
  id,
  works,
  refresh,
  run,
  navigate,
  confirm,
}: TaskProps) {
  const [state, setState] = useListState("tasks", () => ({
    query: "",
    filter: "all",
    type: "all",
    page: 1,
    pageSize: 100,
  }));
  const [pending, setPending] = useState(false);
  async function mutate(task: Task, action: "retry" | "cancel" | "delete") {
    if (action === "delete" && !(await confirm("删除记录", "已有元数据保留")))
      return;
    setPending(true);
    try {
      await api(
        `tasks/${task.id}${action === "delete" ? "" : `/${action}`}`,
        action === "delete" ? "DELETE" : "POST",
      );
      await refresh();
    } finally {
      setPending(false);
    }
  }
  function action(task: Task) {
    const operation = active(task)
      ? "cancel"
      : task.failed || task.partial || task.status === "cancelled"
        ? "retry"
        : null;
    return (
      operation && (
        <Button
          size="sm"
          variant="ghost"
          disabled={pending}
          onClick={() => run(() => mutate(task, operation))}
        >
          {operation === "cancel" ? "取消" : "重试"}
        </Button>
      )
    );
  }
  if (id)
    return (
      <TaskWorks
        key={id}
        id={id}
        tick={tick}
        works={works}
        run={run}
        navigate={navigate}
        cancel={(task) => action(task)}
      />
    );
  const rows = tasks.filter(
    (task) =>
      (state.filter === "all" || task.status === state.filter) &&
      (state.type === "all" || task.type === state.type) &&
      `${task.sequence} #${task.sequence} ${taskTypes[task.type] ?? ""} ${task.params?.codes?.join(" ") ?? ""}`
        .toLowerCase()
        .includes(state.query.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(rows.length / state.pageSize)),
    current = Math.min(state.page, pages);
  return (
    <div className="space-y-4">
      {pending && (
        <p role="status" className="text-sm text-muted-foreground">
          正在更新任务记录…
        </p>
      )}
      <div className="overflow-hidden rounded-lg border">
        <div className="flex flex-wrap gap-3 border-b p-4">
          <SearchInput
            label="搜索任务"
            value={state.query}
            onChange={(query) => setState((s) => ({ ...s, query, page: 1 }))}
          />
          <div className="ml-auto flex items-center gap-3">
            <Select
              label="任务类型"
              value={state.type}
              onChange={(type) => setState((s) => ({ ...s, type, page: 1 }))}
              options={{ all: "全部类型", ...taskTypes }}
            />
            <Select
              label="筛选任务"
              value={state.filter}
              onChange={(filter) =>
                setState((s) => ({ ...s, filter, page: 1 }))
              }
              options={{
                all: "全部任务",
                running: "进行中",
                pending: "等待中",
                finished: "已结束",
                cancelled: "已取消",
              }}
            />
          </div>
        </div>
        <Table className="min-w-[920px] [&_th:first-child]:pl-4 [&_td:first-child]:pl-4">
          <TableHeader>
            <TableRow>
              {["任务", "类型", "时间", "统计", "状态", "操作"].map(
                (label, index) => (
                  <TableHead
                    key={label}
                    className={
                      ["w-20", "w-28", "w-52", "w-80", "w-24", "w-32"][index]
                    }
                  >
                    {label}
                  </TableHead>
                ),
              )}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows
              .slice((current - 1) * state.pageSize, current * state.pageSize)
              .map((task) => (
                <TableRow
                  key={task.id}
                  {...rowNavigation(() => navigate(`tasks/${task.id}`))}
                  aria-label={`查看任务 #${task.sequence}`}
                >
                  <TableCell>#{task.sequence}</TableCell>
                  <TableCell>{taskTypes[task.type] ?? task.type}</TableCell>
                  <TableCell>{formatDate(task.created_at)}</TableCell>
                  <TableCell>
                    <TaskSummary task={task} />
                  </TableCell>
                  <TableCell>
                    <Status status={task.status} />
                  </TableCell>
                  <TableCell onClick={(e) => e.stopPropagation()}>
                    <div className="flex items-center justify-end gap-1">
                      {action(task)}
                      <Button
                        size="sm"
                        variant="destructive-ghost"
                        disabled={pending || active(task)}
                        onClick={() => run(() => mutate(task, "delete"))}
                      >
                        删除
                      </Button>
                    </div>
                  </TableCell>
                </TableRow>
              ))}
          </TableBody>
        </Table>
        {!rows.length && (
          <Empty>
            {state.filter !== "all" || state.type !== "all"
              ? "没有匹配的任务"
              : "暂无记录"}
          </Empty>
        )}
        <Pagination
          page={current}
          pages={pages}
          total={rows.length}
          pageSize={state.pageSize}
          onChange={(page) => setState((s) => ({ ...s, page }))}
          onPageSizeChange={(pageSize) =>
            setState((s) => ({ ...s, pageSize, page: 1 }))
          }
        />
      </div>
    </div>
  );
}

function TaskWorks({
  id,
  tick,
  works,
  run,
  navigate,
  cancel,
}: Pick<TaskProps, "tick" | "works" | "run" | "navigate"> & {
  id: string;
  cancel: (task: Task) => ReactNode;
}) {
  const [state, setState] = useListState(`task:${id}`, () => ({
    query: "",
    filter: "all",
    page: 1,
    pageSize: 100,
    view: defaultListView(),
  }));
  const search = useDebounce(state.query);
  const [detail, setDetail] = useState<TaskDetail | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const workByCode = useMemo(
    () => new Map(works.map((work) => [work.code, work])),
    [works],
  );
  useEffect(() => {
    const controller = new AbortController();
    run(async () => {
      try {
        const value = await api<TaskDetail>(
          `tasks/${id}?page=${state.page}&page_size=${state.pageSize}&status=${state.filter}&query=${encodeURIComponent(search)}`,
          "GET",
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setDetail(value);
          setLoadFailed(false);
        }
      } catch (error) {
        if (!controller.signal.aborted) setLoadFailed(true);
        throw error;
      }
    });
    return () => controller.abort();
  }, [id, state.page, state.pageSize, state.filter, search, tick, run]);
  const rows =
    detail?.items.map(
      (item): Work => ({
        ...(workByCode.get(item.code) ?? {
          code: item.code,
          path: "",
          media_files: [],
          warnings: [],
          category: "",
        }),
        status: item.status,
      }),
    ) ?? [];
  return (
    <div className="space-y-4">
      <Button variant="ghost" onClick={() => navigate("tasks")}>
        <ArrowLeft />
        返回
      </Button>
      {!detail ? (
        <Empty>{loadFailed ? "任务读取失败，请重新进入" : "正在读取…"}</Empty>
      ) : (
        <>
          <div className="flex items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-3">
              <h2 className="text-xl font-semibold">#{detail.sequence}</h2>
              <span className="text-sm text-muted-foreground">
                {taskTypes[detail.type] ?? detail.type}
              </span>
              <Status status={detail.status} />
            </div>
            {active(detail) && cancel(detail)}
          </div>
          <TaskSummary task={detail} />
          {detail.error && (
            <p role="alert" className="text-sm text-destructive">
              {errorText(detail.error)}
            </p>
          )}
          <div className="overflow-hidden rounded-lg border">
            <WorkFilters
              query={state.query}
              filter={state.filter}
              view={state.view}
              onQuery={(query) => setState((s) => ({ ...s, query, page: 1 }))}
              onFilter={(filter) =>
                setState((s) => ({ ...s, filter, page: 1 }))
              }
              onView={(view) => setState((s) => ({ ...s, view }))}
            />
            <WorkList
              works={rows}
              view={state.view}
              pendingLabel="等待中"
              open={(work) =>
                navigate(
                  `library/work/${encodeURIComponent(work.path)}/${detail.id}`,
                )
              }
            />
            {!rows.length && (
              <Empty>
                {state.query || state.filter !== "all"
                  ? "没有匹配的作品"
                  : "暂无任务项"}
              </Empty>
            )}
            <Pagination
              page={detail.page}
              pages={detail.pages}
              total={detail.matched}
              pageSize={state.pageSize}
              onChange={(page) => setState((s) => ({ ...s, page }))}
              onPageSizeChange={(pageSize) =>
                setState((s) => ({ ...s, pageSize, page: 1 }))
              }
            />
          </div>
        </>
      )}
    </div>
  );
}
