import { useState, type ComponentProps } from "react";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

export function ArtworkImage({
  src,
  alt,
  className,
  ...props
}: ComponentProps<"img">) {
  const [failed, setFailed] = useState<string>();
  if (src && failed === src)
    return (
      <span
        role="img"
        aria-label={`${alt ?? "图片"}加载失败`}
        title="图片加载失败，请刷新重试"
        className={cn(
          "inline-flex items-center justify-center whitespace-normal bg-muted p-2 text-xs text-destructive",
          className,
        )}
      >
        加载失败
      </span>
    );
  return (
    <img
      {...props}
      className={className}
      src={src}
      alt={alt}
      onError={(event) => {
        setFailed(typeof src === "string" ? src : undefined);
        props.onError?.(event);
      }}
    />
  );
}

export function ImageViewer({
  src,
  title,
  close,
}: {
  src: string;
  title: string;
  close: () => void;
}) {
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent
        aria-describedby={undefined}
        className="h-[90dvh] grid-rows-[auto_minmax(0,1fr)] overflow-hidden sm:max-w-4xl"
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="flex min-h-0 items-center justify-center overflow-hidden">
          <ArtworkImage
            src={src}
            alt={title}
            className="h-full w-full object-contain"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
