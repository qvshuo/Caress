import { useState } from "react";
import { SecretInput } from "@/components/secret-input";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Field, StickyActions } from "@/components/form-controls";
import { api } from "@/lib/api";
import {
  sources,
  type Settings as Preferences,
  type Source,
} from "@/lib/types";
const categories: Record<string, string> = {
  censored: "有码",
  uncensored: "无码",
  fc2: "FC2",
  unknown: "未知",
};
type Result = { text: string; error: boolean };
export default function Settings({
  initial,
  saved,
  busy,
}: {
  initial: Preferences;
  saved: (value: Preferences) => void;
  busy: boolean;
}) {
  function sourceUrls(settings: Preferences) {
    return Object.fromEntries(
      sources.map((source) => [
        source,
        settings.sites[source]?.base_url ?? settings.source_defaults[source],
      ]),
    ) as Record<Source, string>;
  }
  const [urls, setUrls] = useState(() => sourceUrls(initial));
  const [value, setValue] = useState(() => structuredClone(initial));
  const [routes, setRoutes] = useState(() =>
    Object.fromEntries(
      Object.entries(initial.routes).map(([key, route]) => [
        key,
        route.join(","),
      ]),
    ),
  );
  const [libraries, setLibraries] = useState<
      { id: string; name: string }[] | null
    >(null),
    [results, setResults] = useState<Record<string, Result>>({}),
    [pending, setPending] = useState("");
  function message(kind: string) {
    const result = results[kind];
    return result ? (
      <p
        role={result.error ? "alert" : "status"}
        className={`text-sm ${result.error ? "text-destructive" : "text-muted-foreground"}`}
      >
        {result.text}
      </p>
    ) : null;
  }
  async function execute(kind: string, fn: () => Promise<string>) {
    setPending(kind);
    setResults((old) => {
      const next = { ...old };
      delete next[kind];
      return next;
    });
    try {
      const text = await fn();
      setResults((old) => ({ ...old, [kind]: { text, error: false } }));
    } catch (e) {
      setResults((old) => ({
        ...old,
        [kind]: {
          text: e instanceof Error ? e.message : "操作失败",
          error: true,
        },
      }));
    } finally {
      setPending("");
    }
  }
  function currentSettings() {
    const next: Record<string, Source[]> = {};
    for (const [category, text] of Object.entries(routes)) {
      const list = text.split(",").map((s) => s.trim().toLowerCase());
      if (
        !list.length ||
        list.some((s) => !sources.includes(s as Source)) ||
        new Set(list).size !== list.length
      )
        throw new Error(
          `${categories[category]}来源无效：使用下方来源名称，以英文逗号分隔，不要重复`,
        );
      if (category === "censored" && list.includes("avsox"))
        throw new Error("AVSOX 不支持有码作品，请从有码来源中移除");
      next[category] = list as Source[];
    }
    const sites = Object.fromEntries(
      sources
        .filter(
          (source) =>
            urls[source].trim() &&
            urls[source].trim() !== value.source_defaults[source],
        )
        .map((source) => [source, { base_url: urls[source].trim() }]),
    );
    return { ...value, sites, routes: next };
  }
  return (
    <form
      className="space-y-8"
      onSubmit={(e) => {
        e.preventDefault();
        void execute("save", async () => {
          const next = await api<Preferences>(
            "settings",
            "PUT",
            currentSettings(),
          );
          setValue(next);
          setUrls(sourceUrls(next));
          setRoutes(
            Object.fromEntries(
              Object.entries(next.routes).map(([key, route]) => [
                key,
                route.join(","),
              ]),
            ),
          );
          saved(next);
          return "设置已保存";
        });
      }}
    >
      <fieldset className="space-y-4" disabled={!!pending || busy}>
        <legend className="mb-4 text-base font-medium">Emby</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="服务器地址">
            <Input
              type="url"
              required
              placeholder="http://emby:8096"
              value={value.emby.url}
              onChange={(e) => {
                setLibraries(null);
                setValue((v) => ({
                  ...v,
                  emby: { ...v.emby, url: e.target.value, library_ids: [] },
                }));
              }}
            />
          </Field>
          <Field label="API Key">
            <SecretInput
              configured={value.emby.has_api_key}
              value={value.emby.api_key}
              onChange={(api_key) =>
                setValue((v) => ({ ...v, emby: { ...v.emby, api_key } }))
              }
            />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              void execute("emby", async () => {
                const result = await api<{ server: string; version: string }>(
                  "settings/test/emby",
                  "POST",
                  value,
                );
                return `已连接 ${result.server} · ${result.version}`;
              })
            }
          >
            {pending === "emby" ? "正在测试…" : "测试连接"}
          </Button>
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              void execute("libraries", async () => {
                const rows = await api<{ id: string; name: string }[]>(
                  "settings/libraries",
                  "POST",
                  value,
                );
                setLibraries(rows);
                return rows.length
                  ? `已读取 ${rows.length} 个媒体库`
                  : "没有可用媒体库";
              })
            }
          >
            {pending === "libraries" ? "正在读取…" : "读取媒体库"}
          </Button>
        </div>
        {message("emby")}
        {message("libraries")}
        <div className="space-y-3">
          <p className="text-sm">目标媒体库</p>
          {libraries ? (
            <div className="flex flex-wrap gap-x-6 gap-y-3">
              {libraries.map((lib) => (
                <label
                  key={lib.id}
                  className="inline-flex items-center gap-2 text-sm"
                >
                  <Checkbox
                    checked={value.emby.library_ids.includes(lib.id)}
                    onCheckedChange={(checked) =>
                      setValue((v) => ({
                        ...v,
                        emby: {
                          ...v.emby,
                          library_ids: checked
                            ? [...v.emby.library_ids, lib.id]
                            : v.emby.library_ids.filter((id) => id !== lib.id),
                        },
                      }))
                    }
                  />
                  {lib.name}
                </label>
              ))}
            </div>
          ) : (
            <p className="text-sm text-muted-foreground">
              {value.emby.library_ids.length
                ? `已选择 ${value.emby.library_ids.length} 个媒体库，读取后可更改`
                : "读取媒体库后选择处理范围"}
            </p>
          )}
        </div>
        <p className="text-sm text-muted-foreground">
          请在 Emby 关闭在线元数据提供者。本地
          NFO、扫描和手动刷新仍可能更新元数据。
        </p>
      </fieldset>
      <fieldset className="space-y-4 border-t" disabled={!!pending}>
        <legend className="pr-3 text-base font-medium">翻译</legend>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="API 地址" wide>
            <Input
              type="url"
              value={value.translation.endpoint}
              onChange={(e) =>
                setValue((v) => ({
                  ...v,
                  translation: { ...v.translation, endpoint: e.target.value },
                }))
              }
            />
          </Field>
          <Field label="模型">
            <Input
              value={value.translation.model}
              onChange={(e) =>
                setValue((v) => ({
                  ...v,
                  translation: { ...v.translation, model: e.target.value },
                }))
              }
            />
          </Field>
          <Field label="API Key">
            <SecretInput
              configured={value.translation.has_api_key}
              value={value.translation.api_key}
              onChange={(api_key) =>
                setValue((v) => ({
                  ...v,
                  translation: { ...v.translation, api_key },
                }))
              }
            />
          </Field>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            onClick={() =>
              void execute("translation", async () => {
                const result = await api<{
                  valid: boolean;
                  sample: string;
                  error: string;
                }>("settings/test/translation", "POST", value);
                if (!result.valid) throw new Error(result.error);
                return `测试通过：${result.sample}`;
              })
            }
          >
            {pending === "translation" ? "正在测试…" : "测试翻译"}
          </Button>
          <p className="text-sm text-muted-foreground">
            仅翻译标题与简介，模型留空停用
          </p>
        </div>
        {message("translation")}
      </fieldset>
      <fieldset className="space-y-4 border-t" disabled={!!pending}>
        <legend className="pr-3 text-base font-medium">来源优先级</legend>
        <p className="text-sm text-muted-foreground">
          用英文逗号分隔，按从左到右查询，准确命中后停止
        </p>
        {Object.entries(categories).map(([key, label]) => (
          <Field key={key} label={label}>
            <Input
              value={routes[key] ?? ""}
              className="font-mono text-sm"
              spellCheck={false}
              autoComplete="off"
              onChange={(e) =>
                setRoutes((old) => ({ ...old, [key]: e.target.value }))
              }
            />
          </Field>
        ))}
        <p className="break-words text-xs leading-5 text-muted-foreground">
          可用来源：{sources.join(", ")}
        </p>
        <details className="group border-t pt-4">
          <summary className="cursor-pointer text-sm">来源连接</summary>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="FlareSolverr 地址" wide>
              <Input
                type="url"
                value={value.flaresolverr_url || ""}
                onChange={(e) =>
                  setValue((v) => ({
                    ...v,
                    flaresolverr_url: e.target.value || null,
                  }))
                }
              />
            </Field>
            {sources.map((source) => (
              <Field key={source} label={`${source} 地址`}>
                <Input
                  type="url"
                  value={urls[source]}
                  onChange={(event) =>
                    setUrls((old) => ({ ...old, [source]: event.target.value }))
                  }
                />
              </Field>
            ))}
          </div>
        </details>
      </fieldset>
      <StickyActions>
        {message("save")}
        <Button type="submit" disabled={!!pending}>
          {pending === "save" ? "正在保存…" : "保存设置"}
        </Button>
      </StickyActions>
    </form>
  );
}
