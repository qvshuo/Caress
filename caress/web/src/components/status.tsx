import { Badge } from "@/components/ui/badge";
import { statuses } from "@/lib/labels";

export function Status({ status, label }: { status: string; label?: string }) {
  if (!statuses[status]) return null;
  return (
    <Badge
      className={`${status === "running" ? "border-emerald-600 bg-emerald-600 text-white dark:border-emerald-500 dark:bg-emerald-500 dark:text-emerald-950" : ["found", "success", "scraped"].includes(status) ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : ["partial", "cooldown", "rate_limited"].includes(status) ? "border-amber-500/20 bg-amber-500/10 text-amber-700 dark:text-amber-400" : ""}`}
      variant={
        [
          "failed",
          "invalid_key",
          "conflict",
          "network_error",
          "parse_error",
          "timeout",
          "identity_mismatch",
          "blocked",
          "ip_blocked",
          "region_blocked",
        ].includes(status)
          ? "destructive"
          : "secondary"
      }
    >
      {label ?? statuses[status] ?? status}
    </Badge>
  );
}
