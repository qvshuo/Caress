import type { ReactNode } from "react";
import { Film } from "lucide-react";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { categories, valid } from "@/lib/api";
import type { Work } from "@/lib/types";
import {
  ArtworkImage,
  SelectionCheckbox,
  SearchInput,
  Select,
  Status,
  ViewToggle,
  WorkTitle,
  rowNavigation,
  type ViewMode,
} from "./shared";

export const workFilters = {
  all: "全部状态",
  todo: "待处理",
  running: "进行中",
  done: "已完成",
};
const groups: Record<string, string[]> = {
  todo: [
    "queued",
    "pending",
    "failed",
    "partial",
    "invalid_key",
    "conflict",
    "cancelled",
  ],
  running: ["running"],
  done: ["scraped", "success", "skipped"],
};
export const matchesWorkFilter = (status: string, filter: string) =>
  filter === "all" || !!groups[filter]?.includes(status);

export function WorkFilters({
  query,
  filter,
  view,
  onQuery,
  onFilter,
  onView,
  children,
}: {
  query: string;
  filter: string;
  view: ViewMode;
  onQuery: (value: string) => void;
  onFilter: (value: string) => void;
  onView: (value: ViewMode) => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 border-b p-4">
      <SearchInput value={query} onChange={onQuery} />
      {children}
      <div className="ml-auto flex items-center gap-3">
        <ViewToggle value={view} onChange={onView} />
        <Select
          label="筛选状态"
          value={filter}
          onChange={onFilter}
          options={workFilters}
        />
      </div>
    </div>
  );
}

export function WorkList({
  works,
  view,
  open,
  selection,
  pendingLabel = "未刮削",
}: {
  works: Work[];
  view: ViewMode;
  open: (work: Work) => void;
  pendingLabel?: string;
  selection?: {
    selected: Set<string>;
    toggle: (paths: string[], checked: boolean) => void;
  };
}) {
  const eligible = works.filter(valid);
  const selectAll = selection && (
    <SelectionCheckbox
      {...selection}
      keys={eligible.map((work) => work.path)}
      label="选择本页"
    />
  );
  const select = (work: Work) =>
    selection && (
      <SelectionCheckbox
        {...selection}
        keys={valid(work) ? [work.path] : []}
        label={`选择 ${work.code}`}
      />
    );
  const status = (work: Work) => (
    <Status
      status={work.status}
      label={work.status === "pending" ? pendingLabel : undefined}
    />
  );
  if (view === "grid")
    return (
      <>
        {selection && (
          <div className="flex items-center gap-2 border-b px-4 py-3 text-sm">
            {selectAll}
            <span>选择本页</span>
          </div>
        )}
        <div className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-3 lg:grid-cols-5">
          {works.map((work) => (
            <div
              key={work.path || work.code}
              role={work.path ? "link" : undefined}
              aria-label={`查看 ${work.code}`}
              {...(work.path
                ? rowNavigation(
                    () => open(work),
                    "min-w-0 overflow-hidden rounded-lg border bg-card",
                  )
                : {
                    className:
                      "min-w-0 overflow-hidden rounded-lg border bg-card",
                  })}
            >
              <div className="relative aspect-[2/3] overflow-hidden bg-muted">
                {work.poster ? (
                  <ArtworkImage
                    src={work.poster}
                    alt={`${work.code} 封面`}
                    loading="lazy"
                    className="h-full w-full object-contain"
                  />
                ) : (
                  <div className="flex h-full items-center justify-center text-muted-foreground">
                    <Film className="size-10" aria-label="暂无封面" />
                  </div>
                )}
                {selection && (
                  <div className="absolute top-2 left-2 rounded bg-background/95 p-1.5">
                    {select(work)}
                  </div>
                )}
              </div>
              <div className="space-y-2 p-3 text-sm">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <span className="min-w-0 truncate font-medium">
                    {work.code}
                  </span>
                  {status(work)}
                </div>
                <p
                  className="line-clamp-2 min-h-10"
                  title={work.title || undefined}
                >
                  {work.title || "—"}
                </p>
                <p
                  className="truncate text-muted-foreground"
                  title={work.actors?.join("、")}
                >
                  {work.actors?.join("、") || "—"}
                </p>
                <p className="text-muted-foreground">
                  {categories[work.category] || "—"}
                </p>
                {!!work.warnings.length && (
                  <p className="break-words text-xs text-muted-foreground">
                    {work.warnings.join("；")}
                  </p>
                )}
              </div>
            </div>
          ))}
        </div>
      </>
    );
  return (
    <Table className="min-w-[760px]">
      <TableHeader>
        <TableRow>
          {selection && (
            <TableHead className="w-12 pl-4">{selectAll}</TableHead>
          )}
          <TableHead className={`w-28 ${selection ? "" : "pl-4"}`}>
            作品
          </TableHead>
          <TableHead className="w-[42%]">标题</TableHead>
          <TableHead className="w-[22%]">演员</TableHead>
          <TableHead className="w-20">分类</TableHead>
          <TableHead className="w-24">状态</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {works.map((work) => (
          <TableRow
            key={work.path || work.code}
            aria-label={`查看 ${work.code}`}
            {...(work.path ? rowNavigation(() => open(work)) : {})}
          >
            {selection && (
              <TableCell className="pl-4">{select(work)}</TableCell>
            )}
            <TableCell className={`font-medium ${selection ? "" : "pl-4"}`}>
              {work.code}
              {!!work.warnings.length && (
                <p className="mt-1 whitespace-normal text-xs font-normal text-muted-foreground">
                  {work.warnings.join("；")}
                </p>
              )}
            </TableCell>
            <TableCell>
              <WorkTitle title={work.title} />
            </TableCell>
            <TableCell className="truncate" title={work.actors?.join("、")}>
              {work.actors?.join("、") || "—"}
            </TableCell>
            <TableCell>{categories[work.category] || "—"}</TableCell>
            <TableCell>{status(work)}</TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
