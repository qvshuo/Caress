import { useState, type ComponentProps, type ReactNode } from "react";
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
  ...props
}: ComponentProps<"img"> & { fallback?: ReactNode }) {
  const [failed, setFailed] = useState<string>();
  if (src && failed === src)
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
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setFailed(undefined)}
            >
              重试
            </Button>
          </span>
        )}
      </span>
    );
  return (
    <img
      {...props}
      src={src}
      alt={alt}
      className={className}
      onError={(event) => {
        setFailed(typeof src === "string" ? src : undefined);
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
  function move(delta: number) {
    setIndex((current) =>
      Math.min(images.length - 1, Math.max(0, current + delta)),
    );
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
        onKeyDown={(event) => {
          if (event.altKey || event.ctrlKey || event.metaKey) return;
          if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
            event.preventDefault();
            move(event.key === "ArrowLeft" ? -1 : 1);
          }
        }}
      >
        <DialogHeader>
          <DialogTitle>封面与剧照</DialogTitle>
        </DialogHeader>
        <div className="grid min-h-0 grid-cols-[36px_minmax(0,1fr)_36px] items-center gap-2 sm:gap-4">
          <Button
            size="icon"
            variant="ghost"
            aria-label="上一张"
            aria-disabled={index === 0}
            className="aria-disabled:opacity-50"
            onClick={() => move(-1)}
          >
            <ArrowLeft />
          </Button>
          <div className="flex h-full min-h-0 items-center justify-center">
            <ArtworkImage
              key={image.src}
              src={image.src}
              alt={image.title}
              className="size-full object-contain"
            />
          </div>
          <Button
            size="icon"
            variant="ghost"
            aria-label="下一张"
            aria-disabled={index === images.length - 1}
            className="aria-disabled:opacity-50"
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
