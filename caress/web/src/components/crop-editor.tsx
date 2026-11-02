import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import ReactCrop, { type PercentCrop } from "react-image-crop";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ArtworkImage } from "./shared";
import { errorText } from "@/lib/messages";
import { api } from "@/lib/api";
import { cropBox, percentCrop } from "@/lib/crop";
import type { Box, Crop, ImageChoice } from "@/lib/types";

export default function CropEditor({
  code,
  input,
  image,
  originalImage,
  applyLabel,
  coverPreview,
  busy,
  close,
  applied,
}: {
  code: string;
  input: ImageChoice;
  image: string;
  originalImage: string;
  applyLabel: "应用裁剪" | "设为封面";
  coverPreview?: string;
  busy: boolean;
  close: () => void;
  applied: (crop: Crop) => void;
}) {
  const [result, setResult] = useState<Crop | null>(null);
  const [loadedImage, setLoadedImage] = useState<string>();
  const [showOriginal, setShowOriginal] = useState(false);
  const displayImage = showOriginal ? originalImage : image;
  const [frame, setFrame] = useState<HTMLDivElement | null>(null);
  const [bounds, setBounds] = useState({ width: 0, height: 0 });
  const [dimensions, setDimensions] = useState({ width: 0, height: 0 });
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
  const [selection, setSelection] = useState<PercentCrop>();
  const [pending, setPending] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [error, setError] = useState("");
  const request = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const base = `works/${encodeURIComponent(code)}/crop`;
  const preview = useCallback(
    async (body: { center?: boolean; box?: Box; fingerprint?: string }) => {
      controller.current?.abort();
      const abort = new AbortController();
      controller.current = abort;
      const ticket = ++request.current;
      setPending(true);
      setShowOriginal(true);
      setConfirmed(false);
      setError("");
      try {
        const value = await api<Crop>(
          base,
          "POST",
          { ...input, ...body },
          abort.signal,
        );
        if (abort.signal.aborted || ticket !== request.current) return;
        setResult(value);
        setSelection(percentCrop(value.box, value.width, value.height));
        setConfirmed(true);
      } catch (e) {
        if (!abort.signal.aborted && ticket === request.current)
          setError(errorText(e));
      } finally {
        if (!abort.signal.aborted && ticket === request.current)
          setPending(false);
      }
    },
    [base, input],
  );
  useEffect(() => {
    setResult(null);
    setSelection(undefined);
    setConfirmed(false);
    setPending(false);
    setError("");
    setShowOriginal(false);
    return () => {
      controller.current?.abort();
      request.current++;
    };
  }, [input]);
  const locked = busy || loadedImage !== displayImage;
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent
        aria-describedby={undefined}
        className="h-[90dvh] max-h-[90dvh] grid-rows-[auto_auto_minmax(0,1fr)_auto_auto] overflow-hidden sm:max-w-4xl"
      >
        <DialogHeader>
          <DialogTitle>调整封面</DialogTitle>
        </DialogHeader>
        <div className="flex flex-wrap gap-3">
          <Button
            variant="outline"
            disabled={locked || pending}
            onClick={() => void preview({})}
          >
            人脸裁剪
          </Button>
          <Button
            variant="outline"
            disabled={locked || pending}
            onClick={() => void preview({ center: true })}
          >
            居中裁剪
          </Button>
        </div>
        <div className="grid min-h-0 gap-4 sm:grid-cols-[minmax(0,1fr)_140px]">
          <div
            ref={setFrame}
            className="flex min-h-0 min-w-0 items-center justify-center overflow-hidden rounded-md border bg-muted/30 p-2"
          >
            <ReactCrop
              crop={selection}
              aspect={2 / 3}
              keepSelection
              disabled={locked || !result}
              minWidth={16}
              minHeight={24}
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
              onChange={(_, percent) => {
                controller.current?.abort();
                request.current++;
                setPending(false);
                setConfirmed(false);
                setSelection(percent);
              }}
              onComplete={(_, percent) => {
                if (result && !locked)
                  void preview({
                    box: cropBox(percent, result.width, result.height),
                    fingerprint: result.fingerprint,
                  });
              }}
            >
              <ArtworkImage
                src={displayImage}
                onLoad={(event) => {
                  setLoadedImage(displayImage);
                  setDimensions({
                    width: event.currentTarget.naturalWidth,
                    height: event.currentTarget.naturalHeight,
                  });
                }}
                onError={() => {
                  setLoadedImage(undefined);
                  setError("原图加载失败，请返回后重试");
                }}
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
                draggable={false}
              />
            </ReactCrop>
          </div>
          {(result || coverPreview) && (
            <div className="hidden min-h-0 flex-col items-center gap-2 sm:flex">
              <ArtworkImage
                src={result?.preview ?? coverPreview}
                className="max-h-[calc(100%-1.5rem)] w-auto max-w-32 rounded-md object-contain"
                alt="裁剪预览"
              />
              <p className="text-center text-xs text-muted-foreground">
                2:3 预览
              </p>
            </div>
          )}
        </div>
        <p
          role={error ? "alert" : "status"}
          className={`min-h-5 break-words text-sm ${error ? "text-destructive" : "text-muted-foreground"}`}
        >
          {error ||
            (busy
              ? "作品处理中，暂不能修改"
              : pending
                ? "正在生成预览…"
                : loadedImage !== displayImage
                  ? "正在加载图片…"
                  : "\u00a0")}
        </p>
        <div className="flex justify-end gap-3">
          <Button variant="outline" onClick={close}>
            返回
          </Button>
          <Button
            disabled={locked || pending || !confirmed || !result || !!error}
            onClick={() => {
              if (result && confirmed) applied(result);
            }}
          >
            {applyLabel}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
