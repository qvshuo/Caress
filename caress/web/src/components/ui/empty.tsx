import type { ReactNode } from "react";

export function Empty({ children = "暂无记录" }: { children?: ReactNode }) {
  return (
    <p className="p-12 text-center text-sm text-muted-foreground">{children}</p>
  );
}
