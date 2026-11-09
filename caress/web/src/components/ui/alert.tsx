import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Alert({ className, ...props }: ComponentProps<"div">) {
  return (
    <div
      data-slot="alert"
      role="alert"
      className={cn(
        "relative w-full space-y-2 rounded-lg border bg-background p-3 text-sm",
        className,
      )}
      {...props}
    />
  );
}
