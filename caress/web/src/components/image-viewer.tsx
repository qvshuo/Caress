import { useRef, useState, type ComponentProps, type ReactNode } from "react";
import { ArrowLeft, ArrowRight, ImageOff } from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
export function ArtworkImage({
  src,
  alt,
  className,
  fallback,
  retryable = false,
  ...props
}: ComponentProps<"img"> & { fallback?: ReactNode; retryable?: boolean }) {
  const [retry, setRetry] = useState(0);
  const [failed, setFailed] = useState(false);
  let imageSrc = src;
  if (retry > 0 && typeof src === "string") {
    const url = new URL(src, location.href);
    if (url.protocol === "http:" || url.protocol === "https:") {
      url.searchParams.set("retry", String(retry));
      imageSrc = url.href;
    }
  }
  if (src && failed)
    return (
      <span
        role="img"
        aria-label={fallback ? "暂无头像" : "图片加载失败"}
        className={cn(
          "inline-flex items-center justify-center bg-muted text-muted-foreground",
          className,
        )}
      >
        {fallback || (
          <span className="grid justify-items-center gap-2 text-sm">
            <ImageOff className="size-6" />
            <span>图片加载失败</span>
            {retryable && (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setFailed(false);
                  setRetry((value) => Math.max(value + 1, Date.now()));
                }}
              >
                重试
              </Button>
            )}
          </span>
        )}
      </span>
    );
  return (
    <img
      {...props}
      src={imageSrc}
      alt={alt}
      className={className}
      onError={(event) => {
        setFailed(true);
        props.onError?.(event);
      }}
    />
  );
}
export interface ViewerImage {
  src: string;
  title: string;
}
export function ImageViewer({
  images,
  initialIndex,
  close,
}: {
  images: ViewerImage[];
  initialIndex: number;
  close: () => void;
}) {
  const [index, setIndex] = useState(Math.min(initialIndex, images.length - 1));
  const image = images[index]!;
  const frame = useRef<HTMLDivElement>(null);
  function move(delta: number) {
    const next = Math.min(images.length - 1, Math.max(0, index + delta));
    if (next === 0 || next === images.length - 1) frame.current?.focus();
    setIndex(next);
  }
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent
        aria-describedby={undefined}
        className="h-[90dvh] grid-rows-[auto_minmax(0,1fr)_auto] overflow-hidden sm:max-w-6xl"
      >
        <DialogHeader>
          <DialogTitle>图片</DialogTitle>
        </DialogHeader>
        <div
          ref={frame}
          role="group"
          aria-label="图片浏览"
          tabIndex={0}
          className="grid min-h-0 grid-cols-[36px_minmax(0,1fr)_36px] items-center gap-2 rounded-md outline-none focus-visible:ring-2 focus-visible:ring-ring sm:gap-4"
          onKeyDownCapture={(event) => {
            if (
              event.defaultPrevented ||
              event.altKey ||
              event.ctrlKey ||
              event.metaKey
            )
              return;
            const target = event.target as Element;
            if (
              target.closest(
                "input,textarea,select,[contenteditable=true],[role=combobox]",
              ) ||
              (target.closest("button") &&
                !target.closest("[data-image-navigation]"))
            )
              return;
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
              event.preventDefault();
              move(event.key === "ArrowLeft" ? -1 : 1);
            }
          }}
        >
          <Button
            size="icon"
            variant="ghost"
            data-image-navigation=""
            aria-label="上一张"
            disabled={index === 0}
            onClick={() => move(-1)}
          >
            <ArrowLeft />
          </Button>
          <div className="flex h-full min-h-0 items-center justify-center">
            <ArtworkImage
              retryable
              key={image.src}
              src={image.src}
              alt={image.title}
              className="size-full object-contain"
            />
          </div>
          <Button
            size="icon"
            variant="ghost"
            data-image-navigation=""
            aria-label="下一张"
            disabled={index === images.length - 1}
            onClick={() => move(1)}
          >
            <ArrowRight />
          </Button>
        </div>
        <p
          aria-live="polite"
          className="text-center text-sm tabular-nums text-muted-foreground"
        >
          {index + 1} / {images.length}
        </p>
      </DialogContent>
    </Dialog>
  );
}
