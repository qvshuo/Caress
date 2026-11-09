import type { KeyboardEvent, MouseEvent } from "react";
import { ChevronLeft, ChevronRight, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Select } from "@/components/form-controls";
export function SearchInput({
  value,
  onChange,
}: {
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="relative min-w-40 flex-1">
      <Search className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" />
      <Input
        type="search"
        aria-label="搜索番号或标题"
        placeholder="搜索番号或标题"
        className="pl-9"
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
  onPageSizeChange: (size: number) => void;
}) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm tabular-nums">
      <span className="text-muted-foreground">共 {total} 条</span>
      <div className="flex flex-wrap items-center gap-2">
        <Select
          label="每页条数"
          value={String(pageSize)}
          onChange={(n) => onPageSizeChange(Number(n))}
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
export function SelectionCheckbox({
  ids,
  selected,
  onChange,
  label,
}: {
  ids: string[];
  selected: Set<string>;
  onChange: (ids: string[], checked: boolean) => void;
  label: string;
}) {
  const count = ids.filter((id) => selected.has(id)).length;
  return (
    <Checkbox
      aria-label={label}
      disabled={!ids.length}
      checked={!!ids.length && count === ids.length}
      indeterminate={count > 0 && count < ids.length}
      onCheckedChange={(checked) => onChange(ids, checked === true)}
    />
  );
}
export function rowNavigation(open: () => void) {
  return {
    tabIndex: 0,
    className:
      "cursor-pointer outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
    onClick(event: MouseEvent<HTMLElement>) {
      if (
        (event.target as HTMLElement).closest(
          "button,a,input,label,[role=checkbox],[role=combobox]",
        ) ||
        window.getSelection()?.toString()
      )
        return;
      open();
    },
    onKeyDown(event: KeyboardEvent<HTMLElement>) {
      if (
        event.target === event.currentTarget &&
        ["Enter", " "].includes(event.key)
      ) {
        event.preventDefault();
        open();
      }
    },
  };
}
