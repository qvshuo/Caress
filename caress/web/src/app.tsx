import {
  lazy,
  Suspense,
  useCallback,
  useEffect,
  useRef,
  useState,
} from "react";
import { Library as LibraryIcon, Settings2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Empty } from "@/components/ui/empty";
import {
  ConfirmationDialog,
  type Confirmation,
} from "@/components/confirmation-dialog";
import { Library } from "@/pages/library";
import { BatchProgress } from "@/components/batch-progress";
import { api } from "@/lib/api";
import type {
  Action,
  Batch,
  Settings as Preferences,
  Source,
  WorkNeighbor,
} from "@/lib/types";
import { useListState } from "@/hooks/use-list-state";
import type { LibraryPreferences } from "@/lib/types";
import { version } from "../package.json";
const Settings = lazy(() => import("@/pages/settings"));
const Work = lazy(() => import("@/pages/work"));
export function App() {
  const [list, setList] = useListState<LibraryPreferences>("works", () => ({
    query: "",
    filter: "",
    page: 1,
    pageSize: 100,
  }));
  const [order, setOrder] = useState<WorkNeighbor[]>([]);
  const [route, setRoute] = useState(location.hash.slice(1) || "library");
  const [settings, setSettings] = useState<Preferences | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [stopping, setStopping] = useState(false);
  const [progressOffline, setProgressOffline] = useState(false);
  const stoppingRef = useRef(false);
  const batchRef = useRef<Batch | null>(null);
  const batchGeneration = useRef(0);
  const [revision, setRevision] = useState(0);
  const [notice, setNotice] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const confirmationRef = useRef<Confirmation | null>(null);
  const run = useCallback((fn: () => Promise<unknown>) => {
    void fn().catch((e: unknown) =>
      setNotice(e instanceof Error ? e.message : "操作失败"),
    );
  }, []);
  const refresh = useCallback(() => setRevision((v) => v + 1), []);
  const batchRequest = useRef<{
    generation: number;
    promise: Promise<void>;
  } | null>(null);
  const syncBatch = useCallback(() => {
    const current = batchRequest.current;
    if (current && current.generation === batchGeneration.current)
      return current.promise;
    const generation = ++batchGeneration.current;
    const request = api<Batch | null>("batch").then((value) => {
      if (generation !== batchGeneration.current) return;
      const previous = batchRef.current;
      batchRef.current = value;
      setBatch(value);
      if (
        value?.id !== previous?.id || value?.status !== previous?.status ||
        value?.items.some((item, index) =>
          !["pending", "running"].includes(item.status) &&
          item.status !== previous?.items[index]?.status)
      ) refresh();
    }).finally(() => {
      if (batchRequest.current?.promise === request) batchRequest.current = null;
    });
    batchRequest.current = { generation, promise: request };
    return request;
  }, [refresh]);
  useEffect(() => {
    const sync = () => {
      if (!document.hidden) run(syncBatch);
    };
    window.addEventListener("focus", sync);
    document.addEventListener("visibilitychange", sync);
    return () => {
      window.removeEventListener("focus", sync);
      document.removeEventListener("visibilitychange", sync);
    };
  }, [run, syncBatch]);
  const navigate = useCallback((next: string) => {
    location.hash = next;
    setRoute(next);
  }, []);
  useEffect(() => {
    const hash = () => setRoute(location.hash.slice(1) || "library");
    window.addEventListener("hashchange", hash);
    run(async () => {
      const value = await api<Preferences>("settings");
      setSettings(value);
      await syncBatch();
      if (!value.configured) navigate("settings");
    });
    return () => window.removeEventListener("hashchange", hash);
  }, [run, navigate, syncBatch]);
  const busy = batch?.status === "running";
  useEffect(() => {
    batchRef.current = batch;
  }, [batch]);
  useEffect(() => {
    if (!busy) {
      setProgressOffline(false);
      return;
    }
    let active = true,
      pending = false,
      failures = 0;
    const timer = setInterval(() => {
      if (
        pending || document.hidden ||
        batchRequest.current?.generation === batchGeneration.current
      ) return;
      pending = true;
      const generation = batchGeneration.current;
      void api<Batch | null>("batch")
        .then((value) => {
          if (active && generation === batchGeneration.current) {
            failures = 0;
            setProgressOffline(false);
            const detail = route.startsWith("library/work/")
              ? route.split("/")[2]
              : undefined;
            const result = (b: Batch | null) =>
              b?.items.filter((i) => i.id === detail).at(-1)?.status;
            if (
              (detail &&
                result(value) !== result(batchRef.current) &&
                !["pending", "running"].includes(result(value) || "")) ||
              value?.status !== "running"
            )
              refresh();
            setBatch(value);
          }
        })
        .catch(() => {
          if (active && generation === batchGeneration.current && ++failures >= 3)
            setProgressOffline(true);
        })
        .finally(() => {
          pending = false;
        });
    }, 1500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [busy, refresh, route]);
  const start = async (ids: string[], action: Action, source?: Source) => {
    if (submitting) return false;
    setSubmitting(true);
    try {
      if (action === "force") {
        const accepted = await new Promise<boolean>((resolve) => {
          const value = {
            title: "替换所有的元数据",
            description: "可能覆盖 Emby 中手动修改的相关元数据和封面。继续？",
            resolve,
          };
          confirmationRef.current = value;
          setConfirmation(value);
        });
        if (!accepted) return false;
      }
      batchGeneration.current++;
      const result = await api<Batch>("batch", "POST", {
        ids,
        action,
        source: source || null,
        confirmed: action === "force",
      });
      batchGeneration.current++;
      setBatch(result);
      if (result.status !== "running") refresh();
      return true;
    } finally {
      setSubmitting(false);
    }
  };
  const progress =
    batch ? (
      <BatchProgress
        batch={batch}
        order={order}
        stopping={stopping}
        offline={progressOffline}
        cancel={() =>
          run(async () => {
            if (stoppingRef.current) return;
            stoppingRef.current = true;
            setStopping(true);
            try {
              batchGeneration.current++;
              const result = await api<Batch>("batch/cancel", "POST");
              batchGeneration.current++;
              setBatch(result);
              refresh();
            } finally {
              stoppingRef.current = false;
              setStopping(false);
            }
          })
        }
      />
    ) : null;
  const tab = route.startsWith("settings") ? "settings" : "library";
  const ident = route.startsWith("library/work/")
    ? route.split("/")[2]
    : undefined;
  return (
    <main className="mx-auto max-w-6xl px-4 py-6 sm:px-8">
      <header className="mb-6 flex min-h-9 items-center justify-between gap-4">
        <h1 className="text-xl font-medium">
          {tab === "settings" ? "设置" : ident ? "作品详情" : "作品"}
        </h1>
        <nav
          aria-label="主导航"
          className="ml-auto inline-flex shrink-0 gap-1 rounded-lg bg-muted p-1"
        >
          <Button
            variant="navigation"
            size="navigation"
            aria-current={tab === "library" ? "page" : undefined}
            onClick={() => navigate("library")}
          >
            <LibraryIcon className="size-4" />
            作品
          </Button>
          <Button
            variant="navigation"
            size="navigation"
            aria-current={tab === "settings" ? "page" : undefined}
            onClick={() => navigate("settings")}
          >
            <Settings2 className="size-4" />
            设置
          </Button>
        </nav>
      </header>
      {notice && (
        <Alert
          role="alert"
          className="mb-4 flex items-start justify-between gap-3"
        >
          <span>{notice}</span>
          {settings && (
            <Button size="sm" variant="ghost" onClick={() => setNotice("")}>
              关闭
            </Button>
          )}
        </Alert>
      )}
      {!settings ? (
        notice ? (
          <Empty>
            <Button variant="outline" onClick={() => location.reload()}>
              重试
            </Button>
          </Empty>
        ) : (
          <Empty>正在加载…</Empty>
        )
      ) : (
        <Suspense fallback={<Empty>正在加载…</Empty>}>
          {tab === "settings" ? (
            <Settings
              initial={settings}
              saved={(value) => {
                setSettings(value);
                refresh();
              }}
              busy={!!busy}
            />
          ) : ident ? (
            <Work
              key={ident}
              id={ident}
              order={order}
              open={(neighbor) => {
                setList((s) => ({
                  ...s,
                  page: Math.floor(neighbor.position / s.pageSize) + 1,
                }));
                navigate(`library/work/${neighbor.id}`);
              }}
              revision={revision}
              submitting={submitting}
              progress={progress}
              writing={batch?.current.includes(ident) ?? false}
              start={start}
              run={run}
              refresh={refresh}
              back={() => navigate("library")}
              emby={settings.emby.url}
            />
          ) : (
            <Library
              state={list}
              setOrder={setOrder}
              setState={setList}
              revision={revision}
              configured={settings.configured}
              batch={batch}
              start={start}
              run={run}
              open={(id) => navigate(`library/work/${id}`)}
              refresh={() => {
                refresh();
                run(syncBatch);
              }}
              progress={progress}
              submitting={submitting}
            />
          )}
        </Suspense>
      )}
      <footer className="mt-12 border-t pt-4 text-xs text-muted-foreground">
        Caress {version}
      </footer>
      <ConfirmationDialog
        value={confirmation}
        close={(answer) => {
          confirmationRef.current?.resolve(answer);
          confirmationRef.current = null;
          setConfirmation(null);
        }}
      />
    </main>
  );
}
