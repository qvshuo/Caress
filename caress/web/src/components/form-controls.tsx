import type { ReactNode } from "react";
import {
  Select as SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
export function Field({
  label,
  children,
  wide = false,
}: {
  label: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <label
      className={`grid min-w-0 gap-2 text-sm ${wide ? "sm:col-span-2" : ""}`}
    >
      <span>{label}</span>
      {children}
    </label>
  );
}
export function Select({
  value,
  onChange,
  label,
  options,
  disabled,
  className,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  options: Record<string, string>;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <SelectRoot
      value={value}
      items={options}
      onValueChange={(next) => {
        if (next !== null) onChange(next);
      }}
      disabled={disabled}
    >
      <SelectTrigger aria-label={label} className={className}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {Object.entries(options).map(([id, text]) => (
          <SelectItem key={id} value={id}>
            {text}
          </SelectItem>
        ))}
      </SelectContent>
    </SelectRoot>
  );
}
export function StickyActions({ children }: { children: ReactNode }) {
  return (
    <div className="sticky bottom-0 z-20 -mx-4 flex items-center justify-end gap-4 border-t bg-background/95 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur sm:-mx-8 sm:px-8">
      {children}
    </div>
  );
}
