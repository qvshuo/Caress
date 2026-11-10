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
      className="w-16"
      type="button"
      variant="outline"
      aria-label={label}
      title={label}
      disabled={disabled || pending}
      aria-busy={pending}
      onClick={onClick}
    >
      刷新
    </Button>
  );
}
