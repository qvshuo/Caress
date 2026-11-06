import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useEffectEvent,
  useRef,
  useState,
} from "react";
import {
  Library as LibraryIcon,
  ListTodo,
  UsersRound,
  Settings2,
} from "lucide-react";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { version } from "../package.json";
import { Button } from "@/components/ui/button";
import { Library } from "@/pages/library";
import { Work } from "@/pages/work";
import {
  ConfirmationDialog,
  type Confirmation,
  type Confirm,
} from "@/components/confirmation-dialog";
import { Empty } from "@/components/ui/empty";
import type { EditState } from "@/components/form-controls";
import { errorText } from "@/lib/messages";
import { api } from "@/lib/api";
import { active } from "@/lib/utils";
import type {
  Settings as Preferences,
  Task,
  Work as WorkRecord,
  ActorLibrarySnapshot,
} from "@/lib/types";

const Actors = lazy(() =>
  import("@/pages/actors").then((module) => ({ default: module.Actors })),
);
const Settings = lazy(() =>
  import("@/pages/settings").then((module) => ({
    default: module.Settings,
  })),
);
const Tasks = lazy(() =>
  import("@/pages/tasks").then((module) => ({ default: module.Tasks })),
);
const tabs = [
  ["library", "作品", LibraryIcon],
  ["actors", "演员", UsersRound],
  ["tasks", "任务", ListTodo],
  ["settings", "设置", Settings2],
] as const;
function routeHash() {
  const value = location.hash.slice(1);
  if (/^(library|actors|tasks|settings)$/.test(value)) return value;
  try {
    const parts = value.split("/");
    if (
      (parts[0] === "library" &&
        parts[1] === "work" &&
        (parts.length === 3 || parts.length === 4) &&
        decodeURIComponent(parts[2]!)) ||
      (["tasks", "actors"].includes(parts[0]!) &&
        parts.length === 2 &&
        decodeURIComponent(parts[1]!))
    )
      return value;
  } catch {
    /* Invalid hashes return to the library. */
  }
  return "library";
}
export function App() {
  const [route, setRoute] = useState(routeHash);
  const [returnTo, setReturnTo] = useState("library");
  const [settings, setSettings] = useState<Preferences | null>(null);
  const [works, setWorks] = useState<WorkRecord[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [tick, setTick] = useState(0);
  const polling = useRef(false);
  const previousTasks = useRef<Task[]>([]);
  const workCursor = useRef<string | null>(null);
  const [actorRevision, setActorRevision] = useState(0);
  const [actorCache, setActorCache] = useState<ActorLibrarySnapshot | null>(null);
  const [notice, setNotice] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const confirmationRef = useRef<Confirmation | null>(null);
  const edit = useRef<EditState>({ dirty: false, saving: false });
  const reportEdit = useCallback((value: EditState) => {
    edit.current = value;
  }, []);
  const confirm: Confirm = useCallback(
    (title, description) =>
      new Promise((resolve) => {
        if (confirmationRef.current) return resolve(false);
        const value = { title, description, resolve };
        confirmationRef.current = value;
        setConfirmation(value);
      }),
    [],
  );
  function closeConfirmation(answer: boolean) {
    confirmationRef.current?.resolve(answer);
    confirmationRef.current = null;
    setConfirmation(null);
  }
  const run = useCallback((fn: () => Promise<unknown>) => {
    void fn().catch((error: unknown) => {
      if (!(error instanceof Error && error.name === "AbortError"))
        setNotice(errorText(error));
    });
  }, []);
  const mergeWorks = useCallback((updates: WorkRecord[]) => {
    if (!updates.length) return;
    const byPath = new Map(updates.map((work) => [work.path, work]));
    setWorks((current) => current.map((work) => byPath.get(work.path) ?? work));
  }, []);
  const savedWork = useCallback((work: WorkRecord) => {
    const { code, path, category, status, media_files, warnings, title, actors, poster } = work;
    mergeWorks([{ code, path, category, status, media_files, warnings, title, actors, poster }]);
    setActorRevision((value) => value + 1);
  }, [mergeWorks]);
  const refreshTasks = useCallback(async (poll = false) => {
    const activeIds = previousTasks.current.filter(active).map((task) => task.id);
    if (poll && !activeIds.length) return;
    const incoming = await api<Task[]>(poll ? `tasks?ids=${activeIds.join(",")}` : "tasks");
    const byId = new Map(incoming.map((task) => [task.id, task]));
    if (poll && activeIds.some((id) => !byId.has(id))) {
      // Switching the metadata directory selects another runtime scope and task list.
      const [preferences, catalog, records] = await Promise.all([api<Preferences>("settings"), api<WorkRecord[]>("works"), api<Task[]>("tasks")]);
      setSettings(preferences);
      setWorks(catalog);
      previousTasks.current = records;
      setTasks(records);
      workCursor.current = null;
      setActorRevision((value) => value + 1);
      setTick((value) => value + 1);
      return;
    }
    const next = poll ? previousTasks.current.map((task) => byId.get(task.id) ?? task) : incoming;
    const previousById = new Map(previousTasks.current.map((task) => [task.id, task]));
    const changed = next.filter((task) => {
      const before = previousById.get(task.id);
      return task.progress !== before?.progress || task.status !== before?.status;
    });
    const ids = [...new Set([
      ...[...previousTasks.current, ...next].filter(active).map((task) => task.id),
      ...changed.filter((task) => task.progress).map((task) => task.id),
    ])];
    if (ids.length) {
      const cursor = ids.some((id) => !previousById.has(id)) ? null : workCursor.current;
      const updates = await api<{ time: string; works: WorkRecord[] }>(
        `works/updates?task_ids=${ids.join(",")}${cursor ? `&after=${encodeURIComponent(cursor)}` : ""}`,
      );
      mergeWorks(updates.works);
      workCursor.current = updates.time;
    }
    if (changed.length) setActorRevision((value) => value + 1);
    previousTasks.current = next;
    setTasks(next);
    setTick((value) => value + 1);
  }, [mergeWorks]);
  const refresh = useCallback(async () => {
    const [works, tasks] = await Promise.all([
      api<WorkRecord[]>("works"),
      api<Task[]>("tasks"),
    ]);
    setWorks(works);
    previousTasks.current = tasks;
    workCursor.current = null;
    setTasks(tasks);
    setActorRevision((value) => value + 1);
    setTick((value) => value + 1);
  }, []);
  async function navigate(next: string) {
    if (next === route) return true;
    if (
      edit.current.saving ||
      (edit.current.dirty && !(await confirm("离开页面", "未保存的修改会丢失")))
    )
      return false;
    if (next === "settings") setSettings(await api<Preferences>("settings"));
    if (next.startsWith("library/work/")) setReturnTo(route);
    setRoute(next);
    location.hash = next;
    edit.current = { dirty: false, saving: false };
    return true;
  }
  const onHash = useEffectEvent(async () => {
    const next = routeHash();
    if (!(await navigate(next))) history.replaceState(null, "", `#${route}`);
  });
  const onFocus = useEffectEvent(() => {
    if (document.visibilityState === "visible" && settings?.configured)
      run(refresh);
  });
  const onPoll = useEffectEvent(async () => {
    if (document.visibilityState !== "visible" || polling.current) return;
    polling.current = true;
    try {
      await refreshTasks(true);
    } finally {
      polling.current = false;
    }
  });
  useEffect(() => {
    run(async () => {
      const value = await api<Preferences>("settings");
      setSettings(value);
      if (!value.configured) {
        setRoute("settings");
        history.replaceState(null, "", "#settings");
        return;
      }
      await refresh();
    });
    const hash = () => run(onHash),
      focus = () => onFocus();
    const unload = (event: BeforeUnloadEvent) => {
      if (edit.current.dirty || edit.current.saving) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("hashchange", hash);
    window.addEventListener("focus", focus);
    window.addEventListener("beforeunload", unload);
    return () => {
      window.removeEventListener("hashchange", hash);
      window.removeEventListener("focus", focus);
      window.removeEventListener("beforeunload", unload);
    };
  }, [run, refresh]);
  const busy = tasks.some(active);
  useEffect(() => {
    if (!busy) return;
    const timer = setInterval(() => run(onPoll), 1500);
    return () => clearInterval(timer);
  }, [busy, run]);
  const tab = route.split("/")[0];
  const path = route.startsWith("library/work/")
    ? decodeURIComponent(route.split("/")[2]!)
    : null;
  return (
    <main className="mx-auto max-w-6xl px-4 py-6 sm:px-8">
      <nav
        aria-label="主导航"
        className="mb-6 grid h-9 w-full grid-cols-4 items-center gap-1 rounded-lg bg-muted p-[3px] text-muted-foreground sm:inline-flex sm:w-fit"
      >
        {tabs.map(([id, label, Icon]) => (
          <Button
            key={id}
            variant="navigation"
            size="navigation"
            aria-label={id === "tasks" && busy ? "任务 · 进行中" : label}
            aria-current={tab === id ? "page" : undefined}
            onClick={() => run(() => navigate(id))}
          >
            <Icon className="hidden size-4 sm:block" />
            {label}
            {id === "tasks" && busy && (
              <Badge size="counter" variant="secondary" aria-hidden="true">
                {tasks.filter(active).length}
              </Badge>
            )}
          </Button>
        ))}
      </nav>
      {notice && (
        <Alert
          className="sticky top-2 z-40 mb-4 flex max-h-[40dvh] items-start justify-between gap-3 space-y-0 overflow-auto border-destructive/30 text-destructive shadow-sm"
        >
          <p className="min-w-0 break-words">{notice}</p>
          <Button variant="ghost" size="sm" onClick={() => setNotice("")}>
            关闭
          </Button>
        </Alert>
      )}
      {!settings ? (
        <Empty>{notice ? "连接服务失败，请刷新重试" : "正在连接服务…"}</Empty>
      ) : (
        <Suspense fallback={<Empty>正在载入…</Empty>}>
          {tab === "library" &&
            (path ? (
              <Work
                key={path}
                path={path}
                taskId={route.split("/")[3]}
                revision={works.find((work) => work.path === path)}
                back={() => run(() => navigate(returnTo))}
                run={run}
                refresh={refreshTasks}
                saved={savedWork}
                reportEdit={reportEdit}
              />
            ) : (
              <Library
                works={works}
                configured={settings.configured}
                tasksChanged={refreshTasks}
                refresh={refresh}
                run={run}
                navigate={(next) => run(() => navigate(next))}
              />
            ))}
          {tab === "actors" && (settings.configured ? (
            <Actors
              key={settings.metadata_directory}
              scope={settings.metadata_directory}
              cache={actorCache}
              remember={setActorCache}
              revision={String(actorRevision)}
              busy={busy}
              name={
                route.split("/")[1]
                  ? decodeURIComponent(route.split("/")[1]!)
                  : undefined
              }
              works={works}
              refresh={async () => setActorRevision((value) => value + 1)}
              tasksChanged={refreshTasks}
              run={run}
              navigate={(next) => run(() => navigate(next))}
            />
          ) : <Empty />)}
          {tab === "tasks" && (
            <Tasks
              tick={tasks.some((task) => task.id === route.split("/")[1] && active(task)) ? tick : 0}
              tasks={tasks}
              id={route.split("/")[1]}
              works={works}
              run={run}
              confirm={confirm}
              refresh={refreshTasks}
              navigate={(next) => run(() => navigate(next))}
            />
          )}
          {tab === "settings" && (
            <Settings
              settings={settings}
              tasksActive={busy}
              run={run}
              confirm={confirm}
              reportEdit={reportEdit}
              saved={(value) => {
                setSettings(value);
                run(refresh);
              }}
            />
          )}
        </Suspense>
      )}
      <footer className="mt-8 py-4 text-center text-sm text-muted-foreground">
        Caress · {version}
      </footer>
      <ConfirmationDialog value={confirmation} close={closeConfirmation} />
    </main>
  );
}
