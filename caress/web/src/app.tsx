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
import { api } from "@/lib/api";
import type {
  Action,
  Batch,
  Settings as Preferences,
  Source,
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
  const [route, setRoute] = useState(location.hash.slice(1) || "library");
  const [settings, setSettings] = useState<Preferences | null>(null);
  const [batch, setBatch] = useState<Batch | null>(null);
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
      setBatch(await api<Batch | null>("batch"));
      if (!value.configured) navigate("settings");
    });
    return () => window.removeEventListener("hashchange", hash);
  }, [run, navigate]);
  const busy = batch?.status === "running";
  useEffect(() => {
    if (!busy) return;
    let active = true,
      pending = false;
    const timer = setInterval(() => {
      if (pending || document.hidden) return;
      pending = true;
      void api<Batch | null>("batch")
        .then((value) => {
          if (active) {
            setBatch(value);
            if (value?.status !== "running") refresh();
          }
        })
        .catch((e: Error) => {
          if (active) setNotice(e.message);
        })
        .finally(() => {
          pending = false;
        });
    }, 1500);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [busy, refresh]);
  const start = async (ids: string[], action: Action, source?: Source) => {
    if (action !== "scrape") {
      const accepted = await new Promise<boolean>((resolve) => {
        const value = {
          title: action === "force" ? "重新刮削" : "重试失败作品",
          description:
            "将重新执行完整刮削，并可能覆盖你在 Emby 手动修改的相关元数据及海报。无关字段和图片会保留。",
          resolve,
        };
        confirmationRef.current = value;
        setConfirmation(value);
      });
      if (!accepted) return;
    }
    setBatch(
      await api<Batch>("batch", "POST", {
        ids,
        action,
        source: source || null,
        confirmed: action !== "scrape",
      }),
    );
  };
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
          <Button size="sm" variant="ghost" onClick={() => setNotice("")}>
            关闭
          </Button>
        </Alert>
      )}
      {!settings ? (
        <Empty>正在连接服务…</Empty>
      ) : (
        <Suspense fallback={<Empty>正在载入…</Empty>}>
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
              context={list}
              open={(neighbor) => {
                setList((s) => ({
                  ...s,
                  page: Math.floor(neighbor.position / s.pageSize) + 1,
                }));
                navigate(`library/work/${neighbor.id}`);
              }}
              revision={revision}
              busy={!!busy}
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
              setState={setList}
              revision={revision}
              configured={settings.configured}
              batch={batch}
              start={start}
              run={run}
              open={(id) => navigate(`library/work/${id}`)}
              refresh={refresh}
              cancel={async () =>
                setBatch(await api<Batch>("batch/cancel", "POST"))
              }
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
