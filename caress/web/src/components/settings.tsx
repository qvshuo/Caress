import { lazy, Suspense, useEffect, useState } from "react";
import { FolderOpen, Plus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { errorText } from "@/lib/messages";
import { api, categories, sources, statuses } from "@/lib/api";
import type { Settings as Preferences } from "@/lib/types";
import {
  Empty,
  Field,
  StickyActions,
  type Confirm,
  type EditReporter,
} from "./shared";

const DirectoryBrowser = lazy(() => import("./directory-browser"));
type Cooldowns = Record<string, { cooldown_until: number; cooldown_reason: string }>;
function settingsDraft({
  configured: _configured,
  translation_status: _translationStatus,
  pending_settings: _pendingSettings,
  default_routes: _defaultRoutes,
  source_defaults: _defaults,
  source_intervals: _intervals,
  routes,
  ...value
}: Preferences) {
  return {
    ...value,
    routes: Object.fromEntries(
      Object.entries(routes).map(([key, route]) => [key, route.join(", ")]),
    ),
  };
}
export function Settings({
  settings,
  saved,
  run,
  confirm,
  reportEdit,
  tasksActive,
}: {
  settings: Preferences;
  tasksActive: boolean;
  saved: (settings: Preferences) => void;
  run: (fn: () => Promise<void>) => void;
  confirm: Confirm;
  reportEdit: EditReporter;
}) {
  const [draft, setDraft] = useState(() => settingsDraft(settings));
  const [savedNotice, setSavedNotice] = useState(false);
  const [pending, setPending] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{
    valid: boolean;
    message: string;
    sample?: string;
    elapsed_seconds?: number;
  } | null>(null);
  const [browser, setBrowser] = useState<{
    kind: string;
    path: string;
    choose: (path: string) => void;
  } | null>(null);
  const changed =
    JSON.stringify(draft) !== JSON.stringify(settingsDraft(settings));
  useEffect(() => {
    reportEdit({ dirty: changed, saving: pending });
    return () => reportEdit({ dirty: false, saving: false });
  }, [changed, pending, reportEdit]);
  const translationConfigured =
    !!settings.translation.model.trim() &&
    !!settings.translation.api_key.trim();
  const translationChanged =
    JSON.stringify(draft.translation) !== JSON.stringify(settings.translation);
  function update<K extends keyof typeof draft>(
    key: K,
    value: (typeof draft)[K],
  ) {
    if (key === "translation") setTestResult(null);
    setDraft((current) => ({ ...current, [key]: value }));
  }
  async function save() {
    if (
      settings.metadata_directory !== draft.metadata_directory &&
      !(await confirm("切换目录", "现有资料保留在原文件夹"))
    )
      return;
    setSavedNotice(false);
    setPending(true);
    try {
      const value = await api<Preferences>("settings", "PUT", {
        ...draft,
        routes: Object.fromEntries(
          Object.entries(draft.routes).map(([key, route]) => [
            key,
            route
              .split(/[,，]/)
              .map((source) => source.trim())
              .filter(Boolean),
          ]),
        ),
      });
      setDraft(settingsDraft(value));
      saved(value);
      setSavedNotice(true);
    } finally {
      setPending(false);
    }
  }
  function pathInput(
    kind: "media" | "metadata",
    value: string,
    choose: (path: string) => void,
  ) {
    return (
      <div className="flex gap-2">
        <Input
          aria-label={kind === "media" ? "作品目录" : "元数据目录"}
          required
          value={value}
          onChange={(event) => choose(event.target.value)}
        />
        <Button
          type="button"
          variant="outline"
          size="icon"
          aria-label="选择文件夹"
          onClick={() => setBrowser({ kind, path: value, choose })}
        >
          <FolderOpen />
        </Button>
      </div>
    );
  }
  return (
    <div className="space-y-5">
      <form
        className="space-y-5 pb-8"
        onSubmit={(event) => {
          event.preventDefault();
          run(save);
        }}
      >
        <fieldset disabled={pending} className="space-y-5">
          <section className="space-y-4 rounded-lg border p-4">
            <h3 className="text-lg font-semibold">目录</h3>
            <Field label="作品目录">
              {draft.media_directories.map((path, index) => (
                <div className="flex gap-2" key={index}>
                  <div className="min-w-0 flex-1">
                    {pathInput("media", path, (value) =>
                      update(
                        "media_directories",
                        draft.media_directories.map((old, i) =>
                          i === index ? value : old,
                        ),
                      ),
                    )}
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    aria-label="移除目录"
                    onClick={() =>
                      update(
                        "media_directories",
                        draft.media_directories.filter((_, i) => i !== index),
                      )
                    }
                  >
                    <X />
                  </Button>
                </div>
              ))}
            </Field>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                setBrowser({
                  kind: "media",
                  path: "",
                  choose: (path) =>
                    update("media_directories", [
                      ...draft.media_directories,
                      path,
                    ]),
                })
              }
            >
              <Plus />
              添加目录
            </Button>
            <Field label="元数据目录">
              <div className="pr-11">
                {pathInput("metadata", draft.metadata_directory ?? "", (path) =>
                  update("metadata_directory", path),
                )}
              </div>
            </Field>
          </section>
          <section className="space-y-4 rounded-lg border p-4">
            <div className="flex items-center justify-between gap-3">
              <h3 className="text-lg font-semibold">翻译</h3>
              <Button
                type="button"
                variant="outline"
                disabled={pending || testing}
                onClick={() =>
                  run(async () => {
                    setTesting(true);
                    try {
                      setTestResult(
                        await api(
                          "translation/test",
                          "POST",
                          draft.translation,
                        ),
                      );
                    } catch (error) {
                      setTestResult({
                        valid: false,
                        message:
                          error instanceof Error ? error.message : "测试失败",
                      });
                    } finally {
                      setTesting(false);
                    }
                  })
                }
              >
                {testing ? "测试中" : "测试"}
              </Button>
            </div>
            {!testResult &&
              translationConfigured &&
              !translationChanged &&
              settings.translation_status.valid === false && (
                <p role="alert" className="text-sm text-destructive">
                  {errorText(settings.translation_status.message)}
                </p>
              )}
            {testResult && (
              <div
                role="status"
                className={`rounded-md border p-3 text-sm ${testResult.valid ? "border-emerald-500/30 bg-emerald-500/5" : "border-destructive/30 bg-destructive/5 text-destructive"}`}
              >
                <div className="flex items-start justify-between gap-3">
                  <p className="min-w-0 break-words">
                    {errorText(testResult.message)}
                    {testResult.elapsed_seconds !== undefined
                      ? ` · ${testResult.elapsed_seconds.toFixed(2)} 秒`
                      : ""}
                  </p>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-sm"
                    aria-label="关闭测试结果"
                    onClick={() => setTestResult(null)}
                  >
                    <X />
                  </Button>
                </div>
                {testResult.sample && (
                  <p className="mt-2 text-foreground">{testResult.sample}</p>
                )}
              </div>
            )}
            <fieldset disabled={testing} className="grid gap-4 sm:grid-cols-2">
              <Field label="接口地址">
                <Input
                  type="url"
                  value={draft.translation.endpoint}
                  onChange={(e) =>
                    update("translation", {
                      ...draft.translation,
                      endpoint: e.target.value,
                    })
                  }
                />
              </Field>
              <Field label="模型">
                <Input
                  value={draft.translation.model}
                  onChange={(e) =>
                    update("translation", {
                      ...draft.translation,
                      model: e.target.value,
                    })
                  }
                />
              </Field>
              <Field label="API Key">
                <Secret
                  value={draft.translation.api_key}
                  change={(api_key) =>
                    update("translation", { ...draft.translation, api_key })
                  }
                />
              </Field>
              <Field label="超时">
                <Input
                  type="number"
                  min={0}
                  step="any"
                  value={draft.translation.timeout_seconds}
                  onChange={(e) =>
                    update("translation", {
                      ...draft.translation,
                      timeout_seconds: e.target.valueAsNumber,
                    })
                  }
                />
              </Field>
              <Field label="间隔">
                <Input
                  type="number"
                  min={0}
                  step="any"
                  value={draft.translation.interval_seconds}
                  onChange={(e) =>
                    update("translation", {
                      ...draft.translation,
                      interval_seconds: e.target.valueAsNumber,
                    })
                  }
                />
              </Field>
            </fieldset>
          </section>
          <section className="rounded-lg border p-4">
            <h3 className="text-lg font-semibold">来源</h3>
            <div className="mt-4 space-y-4">
              <Sites
                settings={settings}
                value={draft.sites}
                change={(sites) => update("sites", sites)}
                run={run}
                saving={pending}
              />
              <div className="flex items-center justify-between gap-3">
                <h4 className="text-base font-medium">顺序</h4>
                <Button
                  type="button"
                  variant="ghost"
                  onClick={() =>
                    setDraft({
                      ...draft,
                      routes: Object.fromEntries(
                        Object.entries(settings.default_routes).map(
                          ([key, value]) => [key, value.join(", ")],
                        ),
                      ),
                    })
                  }
                >
                  恢复默认
                </Button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                {Object.keys(draft.routes).map((id) => (
                  <Field label={id === "unknown" ? "其他" : categories[id]!} key={id}>
                    <Input
                      required
                      value={draft.routes[id]}
                      onChange={(e) =>
                        update("routes", {
                          ...draft.routes,
                          [id]: e.target.value,
                        })
                      }
                    />
                  </Field>
                ))}
              </div>
            </div>
          </section>
          <section className="rounded-lg border p-4">
            <h3 className="text-lg font-semibold">网络</h3>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              {(
                [
                  ["timeout_seconds", "请求超时"],
                  ["source_timeout_seconds", "来源超时"],
                  ["retries", "重试次数"],
                  ["retry_delay_seconds", "重试间隔"],
                ] as const
              ).map(([key, label]) => (
                <Field key={key} label={label}>
                  <Input
                    type="number"
                    min={key === "retries" ? 0 : 1}
                    max={key === "retries" ? 3 : undefined}
                    value={draft[key]}
                    onChange={(e) => update(key, e.target.valueAsNumber)}
                  />
                </Field>
              ))}
              <Field label="FlareSolverr">
                <Input
                  type="url"
                  value={draft.flaresolverr_url ?? ""}
                  onChange={(e) =>
                    update("flaresolverr_url", e.target.value || null)
                  }
                />
              </Field>
            </div>
          </section>
        </fieldset>
        {settings.pending_settings && tasksActive && (
          <div role="status" className="text-sm text-muted-foreground">
            <p>设置已保存，后续批次生效</p>
            <p>目录将在队列空闲时切换</p>
          </div>
        )}
        <StickyActions>
          {savedNotice && !changed && (
            <span role="status" className="text-sm text-muted-foreground">
              已保存
            </span>
          )}
          <Button
            type="submit"
            disabled={pending || !changed || !draft.media_directories.length}
          >
            {pending ? "保存中" : "保存"}
          </Button>
        </StickyActions>
      </form>
      {browser && (
        <Suspense fallback={<Empty>正在载入…</Empty>}>
          <DirectoryBrowser
            kind={browser.kind}
            initial={browser.path}
            choose={(path) => {
              browser.choose(path);
              setBrowser(null);
            }}
            close={() => setBrowser(null)}
          />
        </Suspense>
      )}
    </div>
  );
}
function Sites({
  settings,
  value,
  change,
  run,
  saving,
}: {
  settings: Preferences;
  value: Preferences["sites"];
  change: (value: Preferences["sites"]) => void;
  run: (fn: () => Promise<void>) => void;
  saving: boolean;
}) {
  const [cooldowns, setCooldowns] = useState<Cooldowns>({});
  const [clock, setClock] = useState(() => Date.now() / 1000);
  const [resetting, setResetting] = useState<string | null>(null);
  useEffect(() => {
    if (resetting !== null || saving) return;
    const controller = new AbortController();
    let loading = false;
    async function refresh() {
      if (loading || document.hidden) return;
      loading = true;
      try {
        const value = await api<Cooldowns>(
          "sources/cooldowns", "GET", undefined, controller.signal,
        );
        if (!controller.signal.aborted) {
          setCooldowns(value);
          setClock(Date.now() / 1000);
        }
      } finally {
        loading = false;
      }
    }
    const refreshVisible = () => run(refresh);
    refreshVisible();
    const timer = setInterval(refreshVisible, 3000);
    document.addEventListener("visibilitychange", refreshVisible);
    return () => {
      controller.abort();
      clearInterval(timer);
      document.removeEventListener("visibilitychange", refreshVisible);
    };
  }, [run, resetting, saving]);
  const cooling = Object.values(cooldowns).some((row) => row.cooldown_until > clock);
  useEffect(() => {
    if (!cooling) return;
    const timer = setInterval(() => setClock(Date.now() / 1000), 1000);
    return () => clearInterval(timer);
  }, [cooling]);
  return (
    <div className="space-y-4">
      {Object.entries(sources).map(([id, name]) => {
        const site = value[id] ?? {
          base_url: null,
          interval_seconds: null,
        };
        const rows = Object.entries(cooldowns).filter(
          ([key, row]) => (key === id || key.startsWith(`${id}:`)) && row.cooldown_until > clock,
        );
        return (
          <section key={id} className="border-b pb-3 last:border-0">
            <h4 className="text-base font-medium">{name}</h4>
            {rows.length > 0 && (
              <div className="mt-2 grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-3 sm:w-fit sm:max-w-full sm:min-w-[calc((100%_-_0.75rem)/2)]">
                <div className="min-w-0 text-sm text-amber-700 tabular-nums dark:text-amber-400">
                  {rows.map(([key, row]) => (
                    <p key={key} className="break-words">
                      {key.includes(":") ? `${key.slice(key.indexOf(":") + 1)}：` : ""}
                      {statuses[row.cooldown_reason] ?? "来源冷却"}，剩余 {Math.ceil(row.cooldown_until - clock)} 秒
                    </p>
                  ))}
                </div>
                <Button
                  type="button"
                  variant="outline"
                  disabled={resetting !== null}
                  onClick={() =>
                    run(async () => {
                      setResetting(id);
                      try {
                        setCooldowns(await api<Cooldowns>(`sources/${id}/cooldown`, "DELETE"));
                        setClock(Date.now() / 1000);
                      } finally {
                        setResetting(null);
                      }
                    })
                  }
                >
                  解除冷却
                </Button>
              </div>
            )}
            <div className="mt-3 grid gap-3 sm:grid-cols-2">
              <Field label="地址">
                <Input
                  type="url"
                  value={site.base_url ?? ""}
                  placeholder={settings.source_defaults[id]}
                  onChange={(e) =>
                    change({
                      ...value,
                      [id]: {
                        ...site,
                        base_url: e.target.value || null,
                      },
                    })
                  }
                />
              </Field>
              <Field label="间隔">
                <Input
                  type="number"
                  min={0}
                  step="any"
                  value={site.interval_seconds ?? ""}
                  placeholder={String(
                    settings.source_intervals[id] ?? "",
                  )}
                  onChange={(e) =>
                    change({
                      ...value,
                      [id]: {
                        ...site,
                        interval_seconds: e.target.value
                          ? e.target.valueAsNumber
                          : null,
                      },
                    })
                  }
                />
              </Field>
              {id === "javdb" && (
                <Field label="Cookie">
                  <Secret
                    value={site.cookie ?? ""}
                    change={(cookie) =>
                      change({
                        ...value,
                        [id]: { ...site, cookie },
                      })
                    }
                  />
                </Field>
              )}
            </div>
          </section>
        );
      })}
    </div>
  );
}
function Secret({
  value,
  change,
}: {
  value: string;
  change: (value: string) => void;
}) {
  return (
    <Input
      type="password"
      autoComplete="off"
      value={value}
      onFocus={(e) => {
        if (value === "***") e.target.select();
      }}
      onChange={(e) => change(e.target.value)}
    />
  );
}
