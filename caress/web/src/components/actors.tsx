import { useEffect, useState } from "react";
import { ArrowLeft, UserRound } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Table,
  TableHeader,
  TableBody,
  TableHead,
  TableRow,
  TableCell,
} from "@/components/ui/table";
import { api, categories } from "@/lib/api";
import type { ActorLibrarySnapshot, LibraryActor, Work } from "@/lib/types";
import {
  ArtworkImage,
  useSelection,
  SelectionCheckbox,
  ClearSelection,
  ImageViewer,
  Empty,
  SummaryHeader,
  WorkTitle,
  Pagination,
  SearchInput,
  Select,
  Status,
  useDebounce,
  rowNavigation,
  ViewToggle,
  useListState,
  type ViewMode,
} from "./shared";

interface Props {
  scope: string | null;
  cache: ActorLibrarySnapshot | null;
  remember: (value: ActorLibrarySnapshot) => void;
  revision: string;
  busy: boolean;
  name?: string;
  works: Work[];
  refresh: () => Promise<void>;
  tasksChanged: () => Promise<void>;
  run: (fn: () => Promise<void>) => void;
  navigate: (path: string) => void;
}
export function Actors({
  scope,
  cache,
  remember,
  revision,
  busy,
  name,
  works,
  refresh,
  tasksChanged,
  run,
  navigate,
}: Props) {
  const [state, setState] = useListState("actors", {
    query: "",
    filter: "all",
    page: 1,
    pageSize: 100,
    view: "list" as ViewMode,
  });
  const { query, filter, page, pageSize, view } = state;
  const setQuery = (query: string) =>
    setState((s) => ({ ...s, query, page: 1 }));
  const setFilter = (filter: string) =>
    setState((s) => ({ ...s, filter, page: 1 }));
  const setPage = (page: number) => setState((s) => ({ ...s, page }));
  const setPageSize = (pageSize: number) =>
    setState((s) => ({ ...s, pageSize, page: 1 }));
  const search = useDebounce(query);
  const initial = cache?.scope === scope && cache.query === search ? cache.rows : null;
  const [actors, setActors] = useState<LibraryActor[]>(initial ?? []);
  const [loaded, setLoaded] = useState(initial !== null);
  const [loadState, setLoadState] = useState<"loading" | "ready" | "error">(
    initial === null ? "loading" : "ready",
  );
  const selection = useSelection();
  const selectedCount = selection.selected.size;
  const portraitNames = new Set(actors.filter((actor) => actor.portrait_eligible).flatMap((actor) => actor.actors));
  const portraitSelectionAvailable = selectedCount > 0 && [...selection.selected].every(
    (name) => portraitNames.has(name),
  );
  const [pending, setPending] = useState(false);
  useEffect(() => {
    if (name !== undefined) return;
    const controller = new AbortController();
    setLoadState("loading");
    run(async () => {
      try {
        const rows = await api<LibraryActor[]>(
          `actors/library?query=${encodeURIComponent(search)}`,
          "GET",
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setActors(rows);
          setLoaded(true);
          remember({ scope, query: search, rows });
          setLoadState("ready");
        }
      } catch (error) {
        if (!controller.signal.aborted) setLoadState("error");
        throw error;
      }
    });
    return () => controller.abort();
  }, [name, search, revision, run, scope, remember]);
  const rows = actors.filter(
    (a) => filter === "all" || a.portrait_status === filter,
  );
  const pages = Math.max(1, Math.ceil(rows.length / pageSize)),
    current = Math.min(page, pages);
  const pageRows = rows.slice((current - 1) * pageSize, current * pageSize);
  const selectAll = (
    <SelectionCheckbox
      {...selection}
      keys={pageRows
        .filter((actor) => actor.count > 0)
        .flatMap((actor) => actor.actors)}
      label="选择本页"
    />
  );
  const selectActor = (actor: LibraryActor) => (
    <SelectionCheckbox
      {...selection}
      keys={actor.count > 0 ? actor.actors : []}
      label={`选择 ${actor.name}`}
    />
  );
  async function portraits(actor: LibraryActor) {
    setPending(true);
    try {
      const result = await api<{
        portraits: { status: string; error?: string }[];
      }>(`actors/${encodeURIComponent(actor.name)}/portrait`, "POST", {});
      const error = result.portraits.find(
        (item) => item.status === "error" || item.status === "not_found",
      );
      if (error)
        throw new Error(
          error.error ||
            (actor.portrait ? "未找到头像，已保留旧图" : "未找到可用头像"),
        );
      await refresh();
    } finally {
      setPending(false);
    }
  }
  return (
    <>
      {name === undefined && (
        <div className="space-y-4">
          <SummaryHeader
            count={
              loaded ? actors.length : undefined
            }
            unit="位演员"
            suffix={selectedCount ? ` · 已选 ${selectedCount}` : ""}
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              variant="outline"
              disabled={pending || !selectedCount}
              onClick={() => run(async () => {
                setPending(true);
                try {
                  await api("tasks", "POST", {
                    type: "remap_actors", actor_names: Array.from(selection.selected),
                  });
                  await tasksChanged();
                  navigate("tasks");
                } finally { setPending(false); }
              })}
            >重新映射</Button>
            <Button
              disabled={pending || !portraitSelectionAvailable}
              onClick={() =>
                run(async () => {
                  setPending(true);
                  try {
                    await api("tasks", "POST", {
                      type: "update_portraits",
                      actor_names: Array.from(selection.selected),
                    });
                    await tasksChanged();
                    navigate("tasks");
                  } finally {
                    setPending(false);
                  }
                })
              }
            >
              更新头像
            </Button>
          </div>
          <div className="overflow-hidden rounded-lg border">
            <div className="flex flex-wrap items-center gap-3 border-b p-4">
              <SearchInput label="搜索演员" value={query} onChange={setQuery} />
              <ClearSelection selection={selection} />
              <div className="ml-auto flex items-center gap-3">
                <ViewToggle
                  value={view}
                  onChange={(view) => setState((s) => ({ ...s, view }))}
                />
                <Select
                  label="筛选头像"
                  value={filter}
                  onChange={setFilter}
                  options={{
                    all: "全部状态",
                    missing: "缺少头像",
                    ready: "已有头像",
                  }}
                />
              </div>
            </div>
            {view === "grid" ? (
              <>
                <div className="flex items-center gap-2 border-b px-4 py-3 text-sm">
                  {selectAll}
                  <span>选择本页</span>
                </div>
                <div className="grid grid-cols-2 gap-4 p-4 sm:grid-cols-3 lg:grid-cols-5">
                  {pageRows.map((a) => (
                    <div
                      key={a.name}
                      role="link"
                      aria-label={`查看演员 ${a.name}`}
                      {...rowNavigation(
                        () => navigate(`actors/${encodeURIComponent(a.name)}`),
                        "min-w-0 overflow-hidden rounded-lg border",
                      )}
                    >
                      <div className="relative flex aspect-[2/3] items-center justify-center overflow-hidden bg-muted text-muted-foreground">
                        {a.portrait ? (
                          <ArtworkImage
                            src={portraitURL(a)}
                            alt={a.name}
                            loading="lazy"
                            className="h-full w-full object-cover"
                          />
                        ) : (
                          <UserRound className="size-12" />
                        )}
                        <div className="absolute top-2 left-2 rounded bg-background/95 p-1.5">
                          {selectActor(a)}
                        </div>
                      </div>
                      <div className="space-y-1 p-3 text-sm">
                        <p className="truncate font-medium" title={a.name}>
                          {a.name}
                        </p>
                        <p className="text-muted-foreground">
                          {a.count} 部作品
                        </p>
                        {a.status === "ambiguous" && (
                          <p className="text-muted-foreground">别名有歧义</p>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <Table className="[&_th:first-child]:pl-4 [&_td:first-child]:pl-4 [&_th:last-child]:pr-4 [&_td:last-child]:pr-4">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-12">{selectAll}</TableHead>
                    {["头像", "演员", "作品数"].map((v) => (
                      <TableHead
                        key={v}
                        className={
                          v === "头像" ? "w-24" : v === "作品数" ? "w-24" : ""
                        }
                      >
                        {v}
                      </TableHead>
                    ))}
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {pageRows.map((a) => (
                      <TableRow
                        key={a.name}
                        {...rowNavigation(() =>
                          navigate(`actors/${encodeURIComponent(a.name)}`),
                        )}
                        aria-label={`查看演员 ${a.name}`}
                      >
                        <TableCell>{selectActor(a)}</TableCell>
                        <TableCell>
                          {a.portrait ? (
                            <ArtworkImage
                              className="size-12 rounded-md object-cover"
                              src={portraitURL(a)}
                              alt={a.name}
                              loading="lazy"
                            />
                          ) : (
                            <span
                              className="flex size-12 items-center justify-center rounded-md bg-muted text-muted-foreground"
                              aria-label="缺少头像"
                            >
                              <UserRound className="size-5" />
                            </span>
                          )}
                        </TableCell>
                        <TableCell
                          className="truncate font-medium"
                          title={a.name}
                        >
                          {a.name}
                          {a.status === "ambiguous" && (
                            <p className="mt-1 text-sm text-muted-foreground">
                              别名有歧义
                            </p>
                          )}
                        </TableCell>
                        <TableCell>{a.count}</TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
            )}
            {!rows.length && (
              <Empty>
                {loadState === "loading"
                  ? "正在读取演员…"
                  : loadState === "error"
                    ? "演员列表读取失败"
                    : search || filter !== "all"
                      ? "没有匹配的演员"
                      : "暂无记录"}
              </Empty>
            )}
            <Pagination
              page={current}
              pages={pages}
              total={rows.length}
              onChange={setPage}
              pageSize={pageSize}
              onPageSizeChange={setPageSize}
            />
          </div>
        </div>
      )}
      {name !== undefined && (
        <ActorDetail
          key={name}
          revision={revision}
          name={name}
          works={works}
          run={run}
          navigate={navigate}
          disabled={busy || pending}
          portraits={portraits}
        />
      )}
    </>
  );
}
function portraitURL(actor: LibraryActor) {
  return `/api/actors/${encodeURIComponent(actor.portrait_name)}/portrait?v=${actor.portrait_version ?? ""}`;
}
function ActorDetail({
  revision,
  name,
  works,
  run,
  navigate,
  disabled,
  portraits,
}: {
  revision: string;
  name: string;
  works: Work[];
  run: Props["run"];
  navigate: Props["navigate"];
  disabled: boolean;
  portraits: (actor: LibraryActor) => Promise<void>;
}) {
  const [actor, setActor] = useState<LibraryActor | null>(null);
  const [viewPortrait, setViewPortrait] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [loadFailed, setLoadFailed] = useState(false);
  const [query, setQuery] = useState("");
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(100);
  useEffect(() => {
    const controller = new AbortController();
    run(async () => {
      try {
        const rows = await api<LibraryActor[]>(
          `actors/library?query=${encodeURIComponent(name)}`,
          "GET",
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          setActor(rows.find((actor) => actor.name === name) ?? null);
          setLoaded(true);
          setLoadFailed(false);
        }
      } catch (error) {
        if (!controller.signal.aborted) setLoadFailed(true);
        throw error;
      }
    });
    return () => controller.abort();
  }, [name, revision, run]);
  const codes = new Set(actor?.codes);
  const rows = works.filter(
    (w) =>
      codes.has(w.code) &&
      `${w.code} ${w.path} ${w.title ?? ""}`
        .toLowerCase()
        .includes(query.toLowerCase()),
  );
  const pages = Math.max(1, Math.ceil(rows.length / pageSize)),
    current = Math.min(page, pages);
  return (
    <div className="space-y-5">
      <Button variant="ghost" onClick={() => navigate("actors")}>
        <ArrowLeft />
        返回
      </Button>
      {!actor ? (
        <Empty>
          {loadFailed
            ? "演员读取失败，请重新进入"
            : loaded
              ? "演员已不在列表中"
              : "正在读取…"}
        </Empty>
      ) : (
        <>
          <div className="flex flex-wrap items-center justify-between gap-4">
            <div className="min-w-0 flex-1">
              <h2 className="break-words text-xl font-semibold">{actor.name}</h2>
              <p className="mt-1 text-sm text-muted-foreground">
                {actor.count} 部作品
              </p>
            </div>
            <Button
              variant="outline"
              disabled={
                disabled || !actor.codes.length || !actor.portrait_eligible
              }
              onClick={() => run(() => portraits(actor))}
            >
              {actor.portrait ? "更新头像" : "获取头像"}
            </Button>
          </div>
          {disabled && (
            <p role="status" className="text-sm text-muted-foreground">
              任务或头像处理中，请稍候
            </p>
          )}
          {!actor.portrait_eligible && (
            <p className="text-sm text-muted-foreground">
              {actor.status === "ambiguous"
                ? "别名有歧义，暂不能获取头像"
                : "演员名称无效，暂不能获取头像"}
            </p>
          )}
          {!actor.codes.length && (
            <p className="text-sm text-muted-foreground">
              暂无作品，暂不能获取头像
            </p>
          )}
          <div className="grid items-start gap-5 sm:grid-cols-[160px_1fr]">
            <Card>
              <CardContent>
                {actor.portrait ? (
                  <Button
                    variant="artwork"
                    className="h-auto w-full p-0"
                    aria-label={`查看 ${actor.name} 高清头像`}
                    onClick={() => setViewPortrait(true)}
                  >
                    <ArtworkImage
                      className="mx-auto max-h-64 w-full rounded-md object-contain"
                      src={portraitURL(actor)}
                      alt={actor.name}
                    />
                  </Button>
                ) : (
                  <Empty>缺少头像</Empty>
                )}
                {actor.actors.some((value) => value !== actor.name) && (
                  <p className="mt-3 break-words text-sm text-muted-foreground">
                    作品署名：{actor.actors.join("、")}
                  </p>
                )}
              </CardContent>
            </Card>
            <Card className="min-w-0 gap-0 py-0">
              <div className="p-4">
                <SearchInput
                  value={query}
                  onChange={(v) => {
                    setQuery(v);
                    setPage(1);
                  }}
                />
              </div>
              <Table className="min-w-[560px] [&_th:first-child]:pl-4 [&_td:first-child]:pl-4">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-28">作品</TableHead>
                    <TableHead>标题</TableHead>
                    <TableHead className="w-20">分类</TableHead>
                    <TableHead className="w-24">状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows
                    .slice((current - 1) * pageSize, current * pageSize)
                    .map((work) => (
                      <TableRow
                        key={work.path}
                        {...rowNavigation(() =>
                          navigate(
                            `library/work/${encodeURIComponent(work.path)}`,
                          ),
                        )}
                        aria-label={`查看 ${work.code}`}
                      >
                        <TableCell className="font-medium">
                          {work.code}
                        </TableCell>
                        <TableCell>
                          <WorkTitle title={work.title} />
                        </TableCell>
                        <TableCell>{categories[work.category]}</TableCell>
                        <TableCell>
                          <Status
                            status={work.status}
                            label={
                              work.status === "pending" ? "未刮削" : undefined
                            }
                          />
                        </TableCell>
                      </TableRow>
                    ))}
                </TableBody>
              </Table>
              {!rows.length && (
                <Empty>{query ? "没有匹配的作品" : "暂无关联作品"}</Empty>
              )}
              <Pagination
                page={current}
                pages={pages}
                total={rows.length}
                onChange={setPage}
                pageSize={pageSize}
                onPageSizeChange={setPageSize}
              />
            </Card>
          </div>
        </>
      )}
      {viewPortrait && actor?.portrait && (
        <ImageViewer
          src={portraitURL(actor)}
          title={actor.name}
          close={() => setViewPortrait(false)}
        />
      )}
    </div>
  );
}
