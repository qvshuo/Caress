import { toast } from "@/components/ui/sonner";
import { lazy, Suspense, useEffect, useEffectEvent, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { errorText } from "@/lib/messages";
import { api } from "@/lib/api";
import { sources, categories } from "@/lib/labels";
import { formatDate } from "@/lib/utils";
import {
  fields,
  metadataDraft,
  metadataForm,
  variantMetadata,
  type Draft,
  type FieldKey,
} from "@/lib/metadata";
import type {
  Crop,
  ImageChoice,
  Variant,
  FieldInputs,
  WorkDetail,
  Work as WorkRecord,
} from "@/lib/types";
import { ArtworkImage } from "@/components/image-viewer";
import { Empty } from "@/components/ui/empty";
import { Select, StickyActions, type EditReporter } from "@/components/form-controls";
import { Status } from "@/components/status";
import { Diagnostics, Process } from "@/components/work-progress";
import { sourceProgress } from "@/lib/source-progress";

const CropEditor = lazy(() => import("@/components/crop-editor"));
type Editor = {
  detail: WorkDetail;
  draft: Draft;
  inputs: FieldInputs;
  fieldSources: Record<string, string>;
  images: Record<string, ImageChoice>;
  sampleSource?: string;
  posterPreview?: string;
};
export function Work({
  code,
  taskId,
  revision,
  back,
  run,
  refresh,
  saved,
  reportEdit,
}: {
  code: string;
  taskId?: string;
  revision?: WorkRecord;
  back: () => void;
  run: (fn: () => Promise<void>) => void;
  refresh: () => Promise<void>;
  saved: (work: WorkDetail) => void;
  reportEdit: EditReporter;
}) {
  const [editor, setEditor] = useState<Editor | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [pending, setPending] = useState<"save" | "prepare" | "sync" | null>(null);
  const [cropImage, setCropImage] = useState<{
    role: string;
    choice: ImageChoice;
    url: string;
    originalUrl: string;
    coverPreview?: string;
  } | null>(null);
  const [mode, setMode] = useState("fill_missing");
  const changed =
    !!editor &&
    (Object.keys(editor.fieldSources).length > 0 ||
      Object.keys(editor.images).length > 0 ||
      !!editor.sampleSource ||
      JSON.stringify(editor.draft) !==
        JSON.stringify(metadataDraft(editor.detail)));
  async function synchronize(relink = false) {
    if (!editor) return;
    setPending("sync");
    try {
      await api(`works/${encodeURIComponent(editor.detail.code)}/sync?relink=${relink}`, "POST");
      accept(await api<WorkDetail>(`works/detail?code=${encodeURIComponent(editor.detail.code)}`));
    } finally {
      setPending(null);
    }
  }
  function accept(detail: WorkDetail) {
    setEditor({
      detail,
      draft: metadataDraft(detail),
      inputs: {
        original_fields: {
          ...(detail.metadata?.original_title != null ? { title: detail.metadata.original_title } : {}),
          ...detail.selection?.original_fields,
        },
        actor_mappings: detail.selection?.actor_mappings ?? [],
        title_text: detail.selection?.title_text ?? null,
        mapping_fingerprint: detail.selection?.mapping_fingerprint ?? "",
      },
      fieldSources: {},
      images: {},
    });
  }
  const receive = useEffectEvent((detail: WorkDetail) => {
    if (!changed && !pending && !cropImage) accept(detail);
    else
      setEditor((current) =>
        current
          ? {
              ...current,
              detail: {
                ...current.detail,
                variants: detail.variants,
                process: detail.process,
                last_result: detail.last_result,
                health: detail.health,
                warnings: detail.warnings,
                media: detail.media,
                status: detail.status,
                editable: detail.editable,
              },
            }
          : current,
      );
  });
  useEffect(() => {
    const controller = new AbortController();
    run(async () => {
      try {
        const value = await api<WorkDetail>(
          `works/detail?code=${encodeURIComponent(code)}${taskId ? `&task_id=${encodeURIComponent(taskId)}` : ""}`,
          "GET",
          undefined,
          controller.signal,
        );
        if (!controller.signal.aborted) {
          receive(value);
          setLoadFailed(false);
        }
      } catch (error) {
        if (!controller.signal.aborted) setLoadFailed(true);
        throw error;
      }
    });
    return () => controller.abort();
  }, [code, taskId, revision, run]);
  useEffect(() => {
    reportEdit({ dirty: changed, saving: !!pending });
    return () => reportEdit({ dirty: false, saving: false });
  }, [changed, pending, reportEdit]);
  if (!editor)
    return (
      <Empty>{loadFailed ? "作品读取失败，请重新进入" : "正在读取作品…"}</Empty>
    );
  const { detail, draft } = editor;
  const base = `works/${encodeURIComponent(detail.code)}`;
  const updatedAt = detail.process?.events?.at(-1)?.time;
  const locked = !!pending || !detail.editable;
  const processActive = ["pending", "running"].includes(
    detail.process?.status ?? "",
  );
  const scrapeLocked =
    !!pending || ["invalid_key"].includes(detail.status);
  const variants = detail.variants ?? [];
  const route = detail.route_sources ?? variants.map((v) => v.source);
  const artwork = (file: string) =>
    `/api/${base}/artwork?file=${encodeURIComponent(file)}&v=${detail.artwork_versions[file] ?? ""}`;
  function perform(
    fn: () => Promise<void>,
    action: "save" | "prepare" = "prepare",
  ) {
    run(async () => {
      setPending(action);
      try {
        await fn();
      } finally {
        setPending(null);
      }
    });
  }
  function sourceOf(key: string) {
    return (
      editor!.fieldSources[key] ??
      detail.selection?.field_sources?.[key] ??
      "unknown"
    );
  }
  async function chooseField(key: FieldKey, source: string) {
    if (source === "unknown") return;
    if (source === "custom") {
      setEditor((current) =>
        current
          ? {
              ...current,
              fieldSources: { ...current.fieldSources, [key]: "custom" },
            }
          : current,
      );
      return;
    }
    const variant = await api<Variant>(
      `${base}/variants/${source}/preview`,
      "POST",
      {},
    );
    const next = metadataDraft({ ...detail, metadata: variantMetadata(variant) });
    setEditor((current) =>
      current
        ? {
            ...current,
            draft: { ...current.draft, [key]: next[key] },
            inputs: {
              ...current.inputs,
              original_fields: ["title", "plot", "actors"].includes(key)
                ? { ...current.inputs.original_fields, [key]: variant.original_fields?.[key] ?? null }
                : current.inputs.original_fields,
              ...(key === "title" ? { title_text: variant.title_text ?? null } : {}),
              ...(key === "actors" ? { actor_mappings: variant.actor_mappings ?? [], mapping_fingerprint: variant.mapping_fingerprint ?? "" } : {}),
            },
            fieldSources: { ...current.fieldSources, [key]: source },
            detail: {
              ...current.detail,
              variants: variants.map((v) =>
                v.source === source ? variant : v,
              ),
            },
          }
        : current,
    );
  }
  async function scrape() {
    await api("tasks", "POST", {
      type: mode,
      codes: [detail.code],
    });
    await refresh();
  }
  async function save() {
    const result = await api<WorkDetail>(`${base}/metadata`, "PUT", {
      ...metadataForm(draft),
      field_sources: editor!.fieldSources,
      field_inputs: editor!.inputs,
      ...(editor!.sampleSource ? { sample_source: editor!.sampleSource } : {}),
      image_choices: editor!.images,
    });
    accept(result);
    toast.success("已保存");
    saved(result);
  }
  const imageFiles = Object.fromEntries(
    detail.artwork
      .map((file) => [file.match(/-(poster|fanart\d*)\.[^.]+$/)?.[1], file])
      .filter(([role]) => role),
  );
  const imageRoles = [
    ...new Set([
      ...Object.keys(imageFiles).filter(
        (role) => !editor.sampleSource || !/^fanart\d+$/.test(role),
      ),
      ...Object.keys(editor.images),
    ]),
  ];
  const imageGroups = [
    {
      key: "poster",
      label: "封面",
      roles: imageRoles.filter((role) => role === "poster"),
    },
    {
      key: "fanart",
      label: "封面原图",
      roles: imageRoles.filter((role) => role === "fanart"),
    },
    {
      key: "samples",
      label: "剧照",
      roles: imageRoles
        .filter((role) => /^fanart\d+$/.test(role))
        .sort((a, b) => Number(a.slice(6)) - Number(b.slice(6))),
    },
  ];
  function groupSource(group: (typeof imageGroups)[number]) {
    if (group.key === "samples" && editor!.sampleSource)
      return editor!.sampleSource;
    if (group.key === "poster" && editor!.posterPreview) return "custom";
    const values = new Set(
      group.roles.map(
        (role) =>
          editor!.images[role]?.source ??
          detail.selection?.artwork?.roles[role]?.choice?.source ??
          "unknown",
      ),
    );
    return values.size > 1 ? "mixed" : ([...values][0] ?? "unknown");
  }
  function chooseImageSource(group: string, source: string) {
    const items = variants.find((v) => v.source === source)?.metadata?.artwork;
    if (!items) return;
    const images = { ...editor!.images };
    if (group === "samples") {
      for (const role of Object.keys(images))
        if (/^fanart\d+$/.test(role)) delete images[role];
      items
        .map((item, index) => ({ item, index }))
        .filter(({ item }) => item.role === "sample")
        .slice(0, 100)
        .forEach(({ index }, i) => {
          images[`fanart${i + 1}`] = { source, index };
        });
    } else {
      const index = items.findIndex((item) => item.role !== "sample");
      if (index < 0) return;
      images[group] = { source, index };
    }
    setEditor({
      ...editor!,
      images,
      sampleSource: group === "samples" ? source : editor!.sampleSource,
      posterPreview: group === "poster" ? undefined : editor!.posterPreview,
    });
  }
  function imageLabel(role: string) {
    return role === "poster"
      ? "封面"
      : role === "fanart"
        ? "封面原图"
        : `剧照 ${role.slice(6)}`;
  }
  function imageUrl(choice: ImageChoice, poster = false) {
    if (choice.file) return artwork(choice.file);
    return `/api/${base}/variants/${choice.source}/artwork?index=${choice.index}&poster=${poster}`;
  }
  function preview(role: string) {
    if (role === "poster" && editor!.posterPreview)
      return editor!.posterPreview;
    const choice = editor!.images[role];
    if (choice) return imageUrl(choice, role === "poster");
    return imageFiles[role] ? artwork(imageFiles[role]!) : null;
  }
  function openCrop(role: string) {
    const choice =
      editor!.images[role] ??
      (imageFiles[role] ? { source: "custom", file: imageFiles[role] } : null);
    if (!choice) return;
    const original =
      role === "poster"
        ? (editor!.images.fanart ??
          (imageFiles.fanart
            ? { source: "custom", file: imageFiles.fanart }
            : choice))
        : choice;
    const { box: _, fingerprint: __, ...input } = original;
    setCropImage({
      role,
      choice: input,
      url: preview(role) ?? imageUrl(input),
      originalUrl: imageUrl(input),
      coverPreview: preview("poster") ?? undefined,
    });
  }
  function applyCrop(choice: ImageChoice, crop: Crop) {
    setEditor((current) =>
      current
        ? {
            ...current,
            images: {
              ...current.images,
              poster: {
                ...choice,
                box: crop.box,
                fingerprint: crop.fingerprint,
              },
            },
            posterPreview: crop.preview,
          }
        : current,
    );
    setCropImage(null);
  }
  const renderField = ([key, label, type]: (typeof fields)[number]) => {
    const source = sourceOf(key);
    const options: Record<string, string> = {
      custom: "自定义",
      ...Object.fromEntries(
        variants
          .filter((v) => v.status === "found" && v.processed_fields !== undefined)
          .map((v) => [v.source, sources[v.source]!]),
      ),
    };
    if (!(source in options)) options[source] = sources[source] ?? (source === "unknown" ? "来源未知" : source);
    const sourceVariant = variants.find((v) => v.source === source);
    const fieldErrors = editor.fieldSources[key]
      ? sourceVariant?.translation_errors
      : detail.last_result?.translation_errors;
    const original = editor.inputs.original_fields[key];
    const originalText = Array.isArray(original) ? original.map((actor) => actor.name).join("\n") : original ?? "";
    const unchangedActors = key === "actors" && Array.isArray(original) && originalText === draft[key];
    const translated = editor.fieldSources[key]
      ? sourceVariant?.translated_fields?.includes(key)
      : detail.selection?.translated_fields?.includes(key);
    const tag = key === "actors" && source !== "custom" && !unchangedActors && original !== undefined
      ? "主名" : translated && source !== "custom" ? "译" : null;
    const showOriginal = ["title", "plot", "actors"].includes(key) && original !== undefined && !unchangedActors;
    const change = (value: string) =>
      setEditor({
        ...editor,
        draft: { ...draft, [key]: value },
        fieldSources: { ...editor.fieldSources, [key]: "custom" },
      });
    return (
      <div key={key} className="space-y-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <label htmlFor={`metadata-${key}`} className="text-sm font-medium">
              {label}
            </label>
            {tag && <Badge variant="secondary" size="counter">{tag}</Badge>}
          </div>
          <Select
            label={`${label}来源`}
            value={source}
            onChange={(id) => perform(() => chooseField(key, id))}
            disabled={locked}
            options={options}
          />
        </div>
        {["textarea", "list"].includes(type) ? (
          <Textarea
            id={`metadata-${key}`}
            aria-label={tag ? `${label}${tag}` : label}
            disabled={locked}
            required={key === "title"}
            rows={key === "plot" ? 6 : 2}
            value={draft[key]}
            onChange={(e) => change(e.target.value)}
          />
        ) : (
          <Input
            id={`metadata-${key}`}
            aria-label={label}
            type={type}
            disabled={locked}
            min={type === "number" ? 1 : undefined}
            max={type === "number" ? 9999 : undefined}
            value={draft[key]}
            onChange={(e) => change(e.target.value)}
          />
        )}
        {showOriginal && (
          <div className="space-y-2">
            <label htmlFor={`metadata-${key}-original`} className="text-sm text-muted-foreground">{key === "actors" ? "原始姓名" : "原文"}</label>
            <Textarea
              id={`metadata-${key}-original`}
              aria-label={`${label}原文`}
              readOnly
              rows={key === "plot" ? 6 : 2}
              value={originalText}
            />
          </div>
        )}
        {source !== "custom" &&
          fieldErrors
            ?.filter((error) => error.field === key)
            .map((error, index) => (
              <p key={index} className="text-xs text-destructive">
                翻译失败：{errorText(error.error)}
              </p>
            ))}
      </div>
    );
  };
  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-wrap items-center gap-3">
          <Button variant="ghost" size="icon" aria-label="返回" onClick={back}>
            <ArrowLeft />
          </Button>
          <h2 className="text-xl font-semibold">{detail.status === "invalid_key" ? (detail.media?.[0]?.filename ?? detail.code) : detail.code}</h2>
          <Badge variant="secondary">{categories[detail.category]}</Badge>
          <Status
            status={detail.status}
            label={
              detail.status === "pending"
                ? "未刮削"
                : undefined
            }
          />
          {updatedAt && (
            <time
              dateTime={updatedAt}
              className="text-sm text-muted-foreground"
            >
              任务更新于 {formatDate(updatedAt, true)}
            </time>
          )}
        </div>
        <div className="flex flex-wrap gap-2">
          <Select
            label="刮削模式"
            value={mode}
            onChange={setMode}
            disabled={scrapeLocked}
            options={{
              fill_missing: "补齐缺失",
              refresh: "重新刮削",
            }}
          />
          <Button
            disabled={scrapeLocked || changed}
            onClick={() => perform(() => scrape())}
          >
            刮削元数据
          </Button>
        </div>
      </div>
      <form
        className="pb-8"
        onSubmit={(e) => {
          e.preventDefault();
          perform(save, "save");
        }}
      >
        <div className="grid items-start gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
          <div className="min-w-0 space-y-6">
            {detail.warnings.length > 0 && (
              <div
                role="status"
                className="space-y-1 rounded-lg border p-3 text-sm"
              >
                {detail.warnings.map((warning) => (
                  <p key={warning}>{errorText(warning)}</p>
                ))}
              </div>
            )}
            {detail.health?.artwork_problems.map(problem => (
              <p key={problem} role="alert" className="rounded-md border border-destructive/30 p-3 text-sm text-destructive">{problem}</p>
            ))}
            <Diagnostics
              report={detail.last_result}
              title="当前文件问题"
              active={detail.status === "running" || detail.status === "queued"}
            />
            <section aria-label="作品图片" className="min-w-0 space-y-6">
              {imageGroups.map((group) => {
                const current = groupSource(group);
                const available = variants.filter(
                  (variant) =>
                    variant.status === "found" &&
                    variant.metadata?.artwork?.some((item) =>
                      group.key === "samples"
                        ? item.role === "sample"
                        : item.role !== "sample",
                    ),
                );
                return (
                  <section
                    key={group.key}
                    aria-label={group.label}
                    className="min-w-0 space-y-2"
                  >
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="text-sm font-medium">{group.label}</h3>
                      <Select
                        label={`${group.label}来源`}
                        value={current}
                        disabled={locked || !available.length}
                        onChange={(source) =>
                          chooseImageSource(group.key, source)
                        }
                        options={{
                          [current]:
                            sources[current] ??
                            (current === "custom"
                              ? "自定义"
                              : current === "mixed"
                                ? "混合来源"
                                : "来源未知"),
                          ...Object.fromEntries(
                            available.map((variant) => [
                              variant.source,
                              sources[variant.source]!,
                            ]),
                          ),
                        }}
                      />
                    </div>
                    {!available.length && (
                      <p className="text-sm text-muted-foreground">
                        暂无可用来源候选
                      </p>
                    )}
                    <div
                      className={
                        group.key === "samples"
                          ? "grid grid-cols-2 gap-4 sm:grid-cols-4"
                          : "min-w-0"
                      }
                    >
                      {group.roles.map((role) => {
                        const image = preview(role);
                        return image ? (
                          <Button
                            key={role}
                            type="button"
                            variant="artwork"
                            size={
                              role === "poster"
                                ? "poster"
                                : role === "fanart"
                                  ? "fanart"
                                  : "sample"
                            }
                            aria-label={`调整${imageLabel(role)}`}
                            onClick={() => openCrop(role)}
                          >
                            <ArtworkImage
                              src={image}
                              alt={imageLabel(role)}
                              className="h-auto w-auto max-h-full max-w-full object-contain"
                              loading="lazy"
                            />
                          </Button>
                        ) : null;
                      })}
                    </div>
                  </section>
                );
              })}
            </section>
            <section aria-label="作品元数据" className="min-w-0 space-y-4">


              <div className="space-y-4">
                {fields.map(renderField)}
              </div>
              <div className="space-y-2 break-all border-t pt-4 pb-6 text-sm text-muted-foreground">
                <p className="font-medium text-foreground">文件路径</p>
                {(detail.media ?? []).map(file => <div key={file.id} className="space-y-1">
                  <p>{file.path}</p>
                  <p>{[file.subtitles && "中文字幕", file.uncensored && "无码破解", file.part !== null && `分集 ${file.part}`, file.sync?.item_id && `Emby ${file.sync.item_id}`].filter(Boolean).join(" · ")}</p>
                  <p>{file.sync?.error ?? (file.sync?.status === "synced" ? "已同步" : file.sync?.status === "syncing" ? "同步中" : "待同步")}</p>
                </div>)}
                <Button type="button" variant="outline" disabled={changed || !!pending || !detail.editable || !detail.metadata} onClick={() => run(() => synchronize())}>{pending === "sync" ? "同步中" : "同步 Emby"}</Button>
                <Button type="button" variant="outline" disabled={changed || !!pending || !detail.editable || !detail.metadata} onClick={() => run(() => synchronize(true))}>重新关联</Button>
              </div>
            </section>
          </div>
          <section
            aria-label="刮削进度"
            className="min-w-0 space-y-4 rounded-lg border bg-muted/20 p-4 lg:sticky lg:top-4"
          >
            <div className="space-y-3">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-base font-semibold">刮削进度</h3>
                {detail.process?.sequence && (
                  <span className="text-sm text-muted-foreground">
                    {taskId ||
                    detail.process.status === "pending" ||
                    detail.process.status === "running"
                      ? "任务"
                      : "最近任务"}{" "}
                    #{detail.process.sequence}
                  </span>
                )}
              </div>
              <Process
                events={detail.process?.events ?? []}
                running={detail.process?.status === "running"}
              />
            </div>
            <div className="space-y-3">
              {route.map((id) => {
                const state = sourceProgress(detail, id);
                return (
                  <div
                    key={id}
                    className="space-y-2 rounded-lg border bg-background p-3 text-sm"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="font-medium">{sources[id]}</span>
                      {state.status !== "missing" && (
                        <Status status={state.status} label={state.label} />
                      )}
                    </div>
                    {(state.phase || state.elapsed !== undefined) && (
                      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-muted-foreground">
                        {state.elapsed !== undefined && (
                          <span className="tabular-nums">
                            {state.status === "running" ||
                            state.status === "pending"
                              ? "已用"
                              : "用时"}{" "}
                            {state.elapsed.toFixed(1)} 秒
                          </span>
                        )}
                        {state.phase && <span>{state.phase}</span>}
                      </div>
                    )}
                    {state.message && (
                      <p className="break-words text-muted-foreground">
                        {state.message}
                      </p>
                    )}
                  </div>
                );
              })}
            </div>
            <Diagnostics
              report={detail.process?.result}
              title="任务记录问题"
              active={processActive}
              taskRecord
            />
          </section>
        </div>
        <div className="h-8" aria-hidden="true" />
        <StickyActions>
          <Button type="submit" disabled={locked || !changed}>
            {pending === "save" ? "保存中" : "保存"}
          </Button>
        </StickyActions>
      </form>
      {cropImage && (
        <Suspense fallback={<Empty>正在载入裁剪…</Empty>}>
          <CropEditor
            code={detail.status === "invalid_key" ? (detail.media?.[0]?.filename ?? detail.code) : detail.code}
            input={cropImage.choice}
            applyLabel={
              cropImage.role === "poster" || cropImage.role === "fanart"
                ? "应用裁剪"
                : "设为封面"
            }
            image={cropImage.url}
            originalImage={cropImage.originalUrl}
            coverPreview={cropImage.coverPreview}
            busy={locked}
            close={() => setCropImage(null)}
            applied={(crop) => applyCrop(cropImage.choice, crop)}
          />
        </Suspense>
      )}
    </div>
  );
}
