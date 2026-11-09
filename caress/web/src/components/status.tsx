import { Badge } from "@/components/ui/badge";
import { statuses } from "@/lib/labels";
export function Status({
  status,
  title,
}: {
  status: keyof typeof statuses;
  title?: string;
}) {
  return (
    <Badge
      size="counter"
      variant="secondary"
      title={title}
      className={
        status === "success"
          ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          : status === "failed" || status === "rescrape_failed"
            ? "border-destructive/20 bg-destructive/10 text-destructive"
            : status === "running"
              ? "border-primary/20 bg-primary/10 text-foreground"
              : "text-muted-foreground"
      }
    >
      {statuses[status]}
    </Badge>
  );
}
