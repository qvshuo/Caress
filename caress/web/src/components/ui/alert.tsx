import type { ComponentProps } from "react";
import { cn } from "@/lib/utils";

export function Alert({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="alert" role="alert" className={cn("relative w-full space-y-2 rounded-lg border bg-background p-3 text-sm", className)} {...props} />;
}
export function AlertTitle({ className, ...props }: ComponentProps<"p">) {
  return <p data-slot="alert-title" className={cn("font-medium text-foreground", className)} {...props} />;
}
export function AlertDescription({ className, ...props }: ComponentProps<"div">) {
  return <div data-slot="alert-description" className={cn("space-y-1 break-words text-muted-foreground", className)} {...props} />;
}
