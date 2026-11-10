import { lazy, Suspense, useEffect, useState, type ReactNode } from "react";
import {
  ArrowLeft,
  ArrowRight,
  ExternalLink,
  Film,
  UserRound,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Alert } from "@/components/ui/alert";
import { Empty } from "@/components/ui/empty";
import { Status } from "@/components/status";
import { ArtworkImage, ImageViewer } from "@/components/image-viewer";
import { Select } from "@/components/form-controls";
import { api } from "@/lib/api";
import { actions } from "@/lib/labels";
import {
  sources,
  type Action,
  type Source,
  type Work as WorkRecord,
  type WorkNeighbor,
} from "@/lib/types";
const CropEditor = lazy(() => import("@/components/crop-editor"));
export default function Work({
  id,
  order,
  open,
  revision,
  submitting,
  progress,
  writing,
  start,
  run,
  refresh,
  back,
  emby,
}: {
  id: string;
  order: WorkNeighbor[];
  open: (neighbor: WorkNeighbor) => void;
  revision: number;
  submitting: boolean;
  progress: ReactNode;
  writing: boolean;
  start: (ids: string[], action: Action, source?: Source) => Promise<boolean>;
  run: (fn: () => Promise<unknown>) => void;
  refresh: () => void;
  back: () => void;
  emby: string;
}) {
  const [work, setWork] = useState<WorkRecord | null>(null),
    [error, setError] = useState(""),
    [action, setAction] = useState<Action>("scrape"),
    [source, setSource] = useState<Source | "">(""),
    [crop, setCrop] = useState(false),
    [viewer, setViewer] = useState<number | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    setError("");
    setWork(null);
    void api<WorkRecord>(`works/${id}`, "GET", undefined, abort.signal)
      .then((value) => {
        if (!abort.signal.aborted) setWork(value);
      })
      .catch((e: Error) => {
        if (!abort.signal.aborted) setError(e.message);
      });
    return () => abort.abort();
  }, [id, revision]);
  const position = order.findIndex((row) => row.id === id);
  const previous = position > 0 ? order[position - 1] : null,
    next = position >= 0 ? order[position + 1] || null : null;
  const images = work
    ? [
        ...(work.poster ? [{ src: work.poster, title: "封面" }] : []),
        ...work.backdrops.map((src, index) => ({
          src,
          title: `图片 ${index + 1}`,
        })),
      ]
    : [];
  const avatarPlaceholder = (
    <UserRound className="size-8" strokeWidth={1.5} aria-hidden="true" />
  );
  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <div
          aria-label="作品切换"
          className="grid grid-cols-[1fr_auto_1fr] items-center gap-2"
        >
          <Button
            variant="ghost"
            className="justify-self-start"
            disabled={!previous}
            title={previous?.label}
            onClick={() => {
              if (previous) open(previous);
            }}
          >
            <ArrowLeft className="size-4" />
            上一个作品
          </Button>
          <Button variant="ghost" onClick={back}>
            返回
          </Button>
          <Button
            variant="ghost"
            className="justify-self-end"
            disabled={!next}
            title={next?.label}
            onClick={() => {
              if (next) open(next);
            }}
          >
            下一个作品
            <ArrowRight className="size-4" />
          </Button>
        </div>
        {progress}
      </div>
      {error ? (
        <Alert role="alert">
          <p>{error}</p>
          <Button variant="outline" onClick={refresh}>
            重试加载
          </Button>
        </Alert>
      ) : !work ? (
        <Empty>正在加载…</Empty>
      ) : (
        <>
          <div className="grid grid-cols-1 gap-6 sm:grid-cols-[200px_minmax(0,1fr)]">
            <div>
              {work.poster ? (
                <button
                  type="button"
                  aria-label="查看封面"
                  className="block w-full max-w-40 rounded-lg sm:max-w-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  onClick={() => setViewer(0)}
                >
                  <ArtworkImage
                    key={work.poster}
                    src={work.poster}
                    alt="封面"
                    className="w-full rounded-lg"
                  />
                </button>
              ) : (
                <div className="flex aspect-[2/3] w-40 sm:w-full items-center justify-center rounded-lg bg-muted text-muted-foreground">
                  <Film className="size-10" aria-label="暂无封面" />
                </div>
              )}
              <Button
                className="mt-3"
                variant="outline"
                disabled={writing || work.status === "unsupported"}
                onClick={() => setCrop(true)}
              >
                裁剪封面
              </Button>
            </div>
            <div className="space-y-4">
              <div className="flex flex-wrap items-center gap-3">
                <span className="text-sm text-muted-foreground">
                  {work.code || "无法识别番号"}
                </span>
                <Status status={writing ? "running" : work.status} />
              </div>
              <h2 className="wrap-anywhere text-xl font-medium">
                {work.title}
              </h2>
              {work.original_title && work.original_title !== work.title && (
                <p className="wrap-anywhere text-sm text-muted-foreground">
                  {work.original_title}
                </p>
              )}
              {work.error && <Alert role="alert">{work.error}</Alert>}
              <div className="flex flex-wrap items-center gap-2">
                {work.status !== "unsupported" && (
                  <Select
                    label="刮削操作"
                    value={action}
                    onChange={(value) => setAction(value as Action)}
                    options={actions}
                  />
                )}
                {work.status !== "unsupported" && (
                  <Select
                    label="刮削来源"
                    value={source}
                    onChange={(value) => setSource(value as Source | "")}
                    options={{
                      "": "自动选择来源",
                      ...Object.fromEntries(sources.map((s) => [s, s])),
                    }}
                  />
                )}
                {work.status !== "unsupported" && (
                  <Button
                    disabled={submitting}
                    onClick={() =>
                      run(() => start([id], action, source || undefined))
                    }
                  >
                    开始执行
                  </Button>
                )}
              </div>
              <a
                className="inline-flex items-center gap-1.5 text-sm text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                href={emby + work.emby_url}
                target="_blank"
                rel="noreferrer"
              >
                在 Emby 中打开
                <ExternalLink className="size-3.5" />
              </a>
              <dl className="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-2 text-sm [&>dd]:wrap-anywhere">
                {(work.release_date || work.year) && (
                  <>
                    <dt className="text-muted-foreground">发行</dt>
                    <dd>{work.release_date?.slice(0, 10) || work.year}</dd>
                  </>
                )}
                {!!work.studios.length && (
                  <>
                    <dt className="text-muted-foreground">厂商</dt>
                    <dd>{work.studios.join("、")}</dd>
                  </>
                )}
                {!!work.genres.length && (
                  <>
                    <dt className="text-muted-foreground">类型</dt>
                    <dd>{work.genres.join("、")}</dd>
                  </>
                )}
                {!!work.tags.length && (
                  <>
                    <dt className="text-muted-foreground">标签</dt>
                    <dd>{work.tags.join("、")}</dd>
                  </>
                )}
              </dl>
            </div>
          </div>
          {work.plot && (
            <section>
              <h2 className="mb-3 font-medium">简介</h2>
              <p className="whitespace-pre-wrap text-sm leading-6">
                {work.plot}
              </p>
            </section>
          )}
          {!!work.people.length && (
            <section>
              <h2 className="mb-3 font-medium">人物</h2>
              <div className="flex flex-wrap gap-4">
                {work.people.map((person, index) => (
                  <div
                    key={`${person.id || person.name}-${index}`}
                    className="w-20 text-sm"
                  >
                    <div className="mb-2 flex aspect-[2/3] w-full items-center justify-center overflow-hidden rounded-md bg-muted text-muted-foreground">
                      {person.image ? (
                        <ArtworkImage
                          key={person.image}
                          src={person.image}
                          alt={person.name}
                          loading="lazy"
                          className="size-full object-cover"
                          fallback={avatarPlaceholder}
                        />
                      ) : (
                        <span role="img" aria-label="暂无头像">
                          {avatarPlaceholder}
                        </span>
                      )}
                    </div>
                    <p className="wrap-anywhere">{person.name}</p>
                    <p className="text-xs text-muted-foreground">
                      {person.type === "Actor"
                        ? "演员"
                        : person.type === "Director"
                          ? "导演"
                          : person.type}
                    </p>
                  </div>
                ))}
              </div>
            </section>
          )}
          {!!work.backdrops.length && (
            <section>
              <h2 className="mb-3 font-medium">图片</h2>
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
                {work.backdrops.map((url, index) => (
                  <button
                    key={url}
                    type="button"
                    aria-label={`查看图片 ${index + 1}`}
                    className="overflow-hidden rounded-md bg-muted outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    onClick={() => setViewer(index + (work.poster ? 1 : 0))}
                  >
                    <ArtworkImage
                      src={url}
                      alt={`图片 ${index + 1}`}
                      className="aspect-video w-full object-contain"
                      loading="lazy"
                    />
                  </button>
                ))}
              </div>
            </section>
          )}
          {work.related_errors?.map((message, index) => (
            <Alert key={index}>{message}</Alert>
          ))}
          <ul
            aria-label="媒体文件"
            className="space-y-3 border-t pt-4 text-sm text-muted-foreground"
          >
            {[work, ...(work.related || [])].map((row) => (
              <li key={row.id} className="space-y-2">
                {row.media.map((media) => (
                  <div key={media.path} className="space-y-1">
                    <p className="break-all">{media.path}</p>
                    <p className="text-xs">
                      {[
                        media.subtitles ? "字幕" : "",
                        media.uncensored ? "无码破解" : "",
                        media.part ? `第 ${media.part} 分集` : "",
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </p>
                  </div>
                ))}
                {row.id !== work.id && row.error && (
                  <p className="text-destructive">{row.error}</p>
                )}
              </li>
            ))}
          </ul>
          {viewer !== null && images.length > 0 && (
            <ImageViewer
              images={images}
              initialIndex={viewer}
              close={() => setViewer(null)}
            />
          )}
          {crop && (
            <Suspense fallback={<Empty>正在载入…</Empty>}>
              <CropEditor
                id={id}
                source={source || undefined}
                busy={writing}
                close={() => setCrop(false)}
                applied={() => {
                  setCrop(false);
                  refresh();
                }}
              />
            </Suspense>
          )}
        </>
      )}
    </div>
  );
}
