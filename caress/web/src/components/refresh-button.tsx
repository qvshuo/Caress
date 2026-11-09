import { RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
export function RefreshButton({
  onClick,
  disabled,
  pending = false,
  label = "刷新",
}: {
  onClick: () => void;
  disabled?: boolean;
  pending?: boolean;
  label?: string;
}) {
  return (
    <Button
      type="button"
      variant="outline"
      aria-label={label}
      title={label}
      disabled={disabled || pending}
      aria-busy={pending}
      onClick={onClick}
    >
      <RefreshCw className={`size-4 ${pending ? "animate-spin" : ""}`} />
      刷新
    </Button>
  );
}
