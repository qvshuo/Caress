import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import ReactCrop, { type PercentCrop } from "react-image-crop";
import { LoaderCircle, RotateCcw, ScanFace, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import { cropBox, percentCrop } from "@/lib/crop";
import type { Crop, Source } from "@/lib/types";
export default function CropEditor({
  id,
  source,
  busy,
  close,
  applied,
}: {
  id: string;
  source?: Source;
  busy: boolean;
  close: () => void;
  applied: () => void;
}) {
  const [crop, setCrop] = useState<Crop | null>(null),
    [selection, setSelection] = useState<PercentCrop>(),
    [pending, setPending] = useState<"source" | "preview" | null>("source"),
    [error, setError] = useState(""),
    [saving, setSaving] = useState(false);
  const session = useRef(""),
    version = useRef(0);
  const base = `works/${id}/crop`;
  const active = useRef(true);
  const loadingSource = pending === "source";
  const acquire = useCallback(
    async (fetchSource = false) => {
      const ticket = ++version.current;
      setPending("source");
      setError("");
      const query = new URLSearchParams();
      if (source) query.set("source", source);
      if (fetchSource) query.set("fetch_source", "true");
      try {
        const value = await api<Crop>(`${base}/source?${query}`, "POST");
        if (!active.current || ticket !== version.current) {
          void api(`${base}/${value.token}`, "DELETE").catch(() => {});
          return;
        }
        const old = session.current;
        session.current = value.token;
        setCrop(value);
        setSelection(percentCrop(value.box, value.width, value.height));
        if (old) void api(`${base}/${old}`, "DELETE").catch(() => {});
      } catch (e) {
        if (active.current && ticket === version.current)
          setError(e instanceof Error ? e.message : "原图获取失败");
      } finally {
        if (active.current && ticket === version.current) {
          setPending(null);
        }
      }
    },
    [base, source],
  );
  useEffect(() => {
    active.current = true;
    void acquire();
    return () => {
      active.current = false;
      version.current++;
      if (session.current)
        void api(`${base}/${session.current}`, "DELETE").catch(() => {});
    };
  }, [acquire, base]);
  async function preview(box?: Crop["box"], center = false) {
    if (!crop) return;
    const ticket = ++version.current;
    setPending("preview");
    setError("");
    try {
      const value = await api<Crop>(`${base}/preview`, "POST", {
        token: crop.token,
        box,
        center,
      });
      if (ticket !== version.current) return;
      const next = {
        ...value,
        token: crop.token,
        origin: crop.origin,
      };
      setCrop(next);
      setSelection(percentCrop(next.box, next.width, next.height));
    } catch (e) {
      if (ticket === version.current)
        setError(e instanceof Error ? e.message : "裁剪失败");
    } finally {
      if (ticket === version.current) setPending(null);
    }
  }
  const [frame, setFrame] = useState<HTMLDivElement | null>(null);
  const [bounds, setBounds] = useState({ width: 0, height: 0 }),
    [dimensions, setDimensions] = useState({ width: 0, height: 0 });
  useLayoutEffect(() => {
    if (!frame) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry)
        setBounds({
          width: entry.contentRect.width,
          height: entry.contentRect.height,
        });
    });
    observer.observe(frame);
    return () => observer.disconnect();
  }, [frame]);
  const scale =
    dimensions.width && dimensions.height
      ? Math.min(
          1,
          bounds.width / dimensions.width,
          bounds.height / dimensions.height,
        )
      : 0;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !saving) close();
      }}
    >
      <DialogContent
        aria-describedby={undefined}
        className="h-[90dvh] grid-rows-[auto_auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-4xl"
      >
        <DialogHeader>
          <DialogTitle>裁剪</DialogTitle>
        </DialogHeader>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            variant="outline"
            disabled={!crop || !!pending || saving || busy}
            onClick={() => void preview()}
          >
            <ScanFace className="size-4" />
            自动裁剪
          </Button>
          <Button
            variant="outline"
            size="icon"
            aria-label="重置选框"
            title="重置选框"
            disabled={!crop || !!pending || saving || busy}
            onClick={() => void preview(undefined, true)}
          >
            <RotateCcw />
          </Button>
          <Button
            variant="outline"
            className="ml-auto"
            aria-label="重新获取原图"
            aria-busy={loadingSource}
            disabled={saving || busy || !!pending}
            onClick={() => void acquire(true)}
          >
            <RefreshCw
              className={`size-4 ${loadingSource ? "animate-spin" : ""}`}
            />
            重新获取原图
          </Button>
        </div>
        {crop ? (
          <div className="grid min-h-0 grid-rows-[minmax(0,1fr)_112px] gap-4 sm:grid-cols-[minmax(0,1fr)_144px] sm:grid-rows-1">
            <div
              ref={setFrame}
              className="flex min-h-0 min-w-0 items-center justify-center overflow-hidden rounded-md bg-muted p-2"
            >
              <ReactCrop
                crop={selection}
                aspect={2 / 3}
                keepSelection
                disabled={saving || busy || loadingSource}
                onChange={(_, p) => {
                  version.current++;
                  setPending("preview");
                  setSelection(p);
                }}
                onComplete={(_, p) =>
                  void preview(cropBox(p, crop.width, crop.height))
                }
                ariaLabels={{
                  cropArea: "裁剪选框",
                  nwDragHandle: "左上缩放",
                  nDragHandle: "上侧缩放",
                  neDragHandle: "右上缩放",
                  eDragHandle: "右侧缩放",
                  seDragHandle: "右下缩放",
                  sDragHandle: "下侧缩放",
                  swDragHandle: "左下缩放",
                  wDragHandle: "左侧缩放",
                }}
              >
                <img
                  src={`/api/${base}/${crop.token}/image`}
                  alt="裁剪原图"
                  className="block max-h-full max-w-full object-contain"
                  style={
                    scale > 0
                      ? {
                          width: dimensions.width * scale,
                          height: dimensions.height * scale,
                        }
                      : undefined
                  }
                  onLoad={(event) =>
                    setDimensions({
                      width: event.currentTarget.naturalWidth,
                      height: event.currentTarget.naturalHeight,
                    })
                  }
                  draggable={false}
                  onError={() => setError("原图加载失败，请刷新重试")}
                />
              </ReactCrop>
            </div>
            <div className="flex min-h-0 items-center justify-center gap-3 sm:flex-col">
              <img
                src={crop.preview}
                alt="裁剪预览"
                className="max-h-full max-w-28 rounded-md object-contain sm:max-w-full"
              />
              <span className="text-xs text-muted-foreground">2:3</span>
            </div>
          </div>
        ) : (
          <div
            role="status"
            className="flex min-h-0 items-center justify-center gap-2 text-sm text-muted-foreground"
          >
            {pending && <LoaderCircle className="size-4 animate-spin" />}
            {pending ? "加载中…" : ""}
          </div>
        )}
        <div className="flex flex-wrap items-center justify-end gap-3">
          <span className="mr-auto text-xs text-muted-foreground">
            {crop?.origin}
          </span>
          {error && (
            <p role="alert" className="mr-auto text-sm text-destructive">
              {error}
            </p>
          )}
          <Button variant="outline" disabled={saving} onClick={close}>
            取消
          </Button>
          <Button
            aria-busy={saving}
            disabled={!crop || !!pending || saving || busy || !!error}
            onClick={() => {
              if (!crop) return;
              setSaving(true);
              void api(`${base}/apply`, "POST", {
                token: crop.token,
                box: crop.box,
              })
                .then(applied)
                .catch((e: Error) => setError(e.message))
                .finally(() => setSaving(false));
            }}
          >
            {saving && <LoaderCircle className="size-4 animate-spin" />}保存
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
