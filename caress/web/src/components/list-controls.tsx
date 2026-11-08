import type { ReactNode, MouseEvent, KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight, Search, LayoutGrid, List } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "./form-controls";
import type { useSelection } from "@/hooks/use-selection";

export function SummaryHeader({
  count,
  unit,
  suffix = "",
  children,
}: {
  count?: number;
  unit: string;
  suffix?: string;
  children?: ReactNode;
}) {
  return (
    <div className="flex min-h-9 flex-wrap items-center justify-between gap-3">
      {count !== undefined && (
        <p className="text-base leading-6 font-medium tabular-nums">
          {count} {unit}{suffix}
        </p>
      )}
      {children}
    </div>
  );
}

export function SearchInput({
  value,
  onChange,
  label = "搜索作品",
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  label?: string;
  disabled?: boolean;
}) {
  return (
    <div className="relative min-w-40 flex-1">
      <Search className="pointer-events-none absolute top-2.5 left-3 size-4 text-muted-foreground" />
      <Input
        type="search"
        disabled={disabled}
        className="pl-9"
        aria-label={label}
        placeholder={label}
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}

export function Pagination({
  page,
  pages,
  total,
  onChange,
  pageSize,
  onPageSizeChange,
}: {
  page: number;
  pages: number;
  total: number;
  onChange: (page: number) => void;
  pageSize: number;
  onPageSizeChange: (pageSize: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm tabular-nums">
      <span className="text-muted-foreground">共 {total} 条</span>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          label="每页条数"
          value={String(pageSize)}
          onChange={(value) => {
            onPageSizeChange(Number(value));
            onChange(1);
          }}
          options={{
            100: "100 条/页",
            1000: "1000 条/页",
            10000: "10000 条/页",
          }}
        />
        <Button
          variant="outline"
          size="icon"
          aria-label="上一页"
          disabled={page <= 1}
          onClick={() => onChange(page - 1)}
        >
          <ChevronLeft />
        </Button>
        <Select
          label="页码"
          value={String(page)}
          onChange={(n) => onChange(Number(n))}
          options={Object.fromEntries(
            Array.from({ length: pages }, (_, i) => [
              i + 1,
              `${i + 1} / ${pages}`,
            ]),
          )}
        />
        <Button
          variant="outline"
          size="icon"
          aria-label="下一页"
          disabled={page >= pages}
          onClick={() => onChange(page + 1)}
        >
          <ChevronRight />
        </Button>
      </div>
    </div>
  );
}

// Row navigation leaves embedded controls and text selection independent.
export function rowNavigation(open: () => void, className = "") {
  return {
    tabIndex: 0,
    className: `cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring ${className}`,
    onClick(event: MouseEvent<HTMLElement>) {
      if (
        (event.target as HTMLElement).closest(
          "button,a,input,label,summary,[role=checkbox],[role=combobox]",
        ) ||
        window.getSelection()?.toString()
      )
        return;
      open();
    },
    onKeyDown(event: KeyboardEvent<HTMLElement>) {
      if (
        event.target !== event.currentTarget ||
        !["Enter", " "].includes(event.key)
      )
        return;
      event.preventDefault();
      open();
    },
  };
}

export type ViewMode = "list" | "grid";

export function defaultListView(): ViewMode {
  return window.matchMedia("(min-width: 640px)").matches ? "list" : "grid";
}

export function ViewToggle({
  value,
  onChange,
}: {
  value: ViewMode;
  onChange: (value: ViewMode) => void;
}) {
  return (
    <div
      role="group"
      aria-label="查看方式"
      className="inline-flex shrink-0 gap-0.5 rounded-md border p-0.5"
    >
      {(
        [
          ["list", "列表", List],
          ["grid", "大图", LayoutGrid],
        ] as const
      ).map(([mode, label, Icon]) => (
        <Button
          key={mode}
          type="button"
          variant={value === mode ? "secondary" : "ghost"}
          size="icon-sm"
          aria-label={label}
          aria-pressed={value === mode}
          onClick={() => onChange(mode)}
        >
          <Icon />
        </Button>
      ))}
    </div>
  );
}

export function SelectionCheckbox({
  keys,
  selected,
  toggle,
  label,
}: {
  keys: string[];
  selected: Set<string>;
  toggle: (keys: string[], checked: boolean) => void;
  label: string;
}) {
  return (
    <Checkbox
      aria-label={label}
      disabled={!keys.length}
      checked={keys.length > 0 && keys.every((key) => selected.has(key))}
      indeterminate={keys.some((key) => selected.has(key)) && !keys.every((key) => selected.has(key))}
      onCheckedChange={(checked) => toggle(keys, checked === true)}
    />
  );
}

export function ClearSelection({
  selection,
}: {
  selection: ReturnType<typeof useSelection>;
}) {
  return selection.selected.size ? (
    <Button variant="ghost" size="sm" onClick={selection.clear}>
      清除选择
    </Button>
  ) : null;
}
