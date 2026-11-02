import {
  useEffect,
  useState,
  type ReactNode,
  type ComponentProps,
  type MouseEvent,
  type KeyboardEvent,
  type Dispatch,
  type SetStateAction,
} from "react";
import {
  ChevronLeft,
  ChevronRight,
  Search,
  LayoutGrid,
  List,
} from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select as SelectRoot,
  SelectTrigger,
  SelectValue,
  SelectContent,
  SelectItem,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";
import { statuses } from "@/lib/api";
import { cn } from "@/lib/utils";

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
export function Select({
  value,
  onChange,
  label,
  options,
  disabled,
}: {
  value: string;
  onChange: (value: string) => void;
  label: string;
  options: Record<string, string>;
  disabled?: boolean;
}) {
  return (
    <SelectRoot value={value} onValueChange={onChange} disabled={disabled}>
      <SelectTrigger aria-label={label} className="max-w-full">
        <SelectValue />
      </SelectTrigger>
      <SelectContent position="popper">
        {Object.entries(options).map(([id, text]) => (
          <SelectItem key={id} value={id}>
            {text}
          </SelectItem>
        ))}
      </SelectContent>
    </SelectRoot>
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
    <div className="flex flex-wrap items-center justify-between gap-3 border-t px-4 py-3 text-sm">
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
export function ArtworkImage({
  src,
  alt,
  className,
  ...props
}: ComponentProps<"img">) {
  const [failed, setFailed] = useState<string>();
  if (src && failed === src)
    return (
      <span
        role="img"
        aria-label={`${alt ?? "图片"}加载失败`}
        title="图片加载失败，请刷新重试"
        className={cn(
          "inline-flex items-center justify-center whitespace-normal bg-muted p-2 text-xs text-destructive",
          className,
        )}
      >
        加载失败
      </span>
    );
  return (
    <img
      {...props}
      className={className}
      src={src}
      alt={alt}
      onError={(event) => {
        setFailed(typeof src === "string" ? src : undefined);
        props.onError?.(event);
      }}
    />
  );
}

export function ImageViewer({
  src,
  title,
  close,
}: {
  src: string;
  title: string;
  close: () => void;
}) {
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent
        aria-describedby={undefined}
        className="h-[90dvh] grid-rows-[auto_minmax(0,1fr)] overflow-hidden sm:max-w-4xl"
      >
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <div className="flex min-h-0 items-center justify-center overflow-hidden">
          <ArtworkImage
            src={src}
            alt={title}
            className="h-full w-full object-contain"
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}

export function Empty({ children = "暂无记录" }: { children?: ReactNode }) {
  return (
    <p className="p-12 text-center text-sm text-muted-foreground">{children}</p>
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
      {label}
      {children}
    </label>
  );
}
export function useDebounce<T>(value: T, delay = 250): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return settled;
}

export type ViewMode = "list" | "grid";
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

export function useListState<T extends object>(
  key: string,
  initial: T,
): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => {
    try {
      return {
        ...initial,
        ...JSON.parse(sessionStorage.getItem(`caress:${key}`) ?? "{}"),
      };
    } catch {
      return initial;
    }
  });
  useEffect(() => {
    try {
      sessionStorage.setItem(`caress:${key}`, JSON.stringify(value));
    } catch {
      /* List preferences remain usable when browser storage is unavailable. */
    }
  }, [key, value]);
  return [value, setValue];
}
export interface Confirmation {
  title: string;
  description: ReactNode;
  resolve: (answer: boolean) => void;
}
export type Confirm = (
  title: string,
  description: ReactNode,
) => Promise<boolean>;
export function ConfirmationDialog({
  value,
  close,
}: {
  value: Confirmation | null;
  close: (answer: boolean) => void;
}) {
  return (
    <Dialog
      open={!!value}
      onOpenChange={(open) => {
        if (!open) close(false);
      }}
    >
      <DialogContent className="max-h-[85dvh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>{value?.title}</DialogTitle>
          <DialogDescription asChild>
            <div className="whitespace-pre-wrap break-words">
              {value?.description}
            </div>
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => close(false)}>
            取消
          </Button>
          <Button onClick={() => close(true)}>确认</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

export interface EditState {
  dirty: boolean;
  saving: boolean;
}
export type EditReporter = (state: EditState) => void;

export function StickyActions({ children }: { children: ReactNode }) {
  return (
    <div className="sticky bottom-0 z-20 -mx-4 flex items-center justify-end gap-4 border-t bg-background/95 px-4 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] backdrop-blur sm:-mx-8 sm:px-8">
      {children}
    </div>
  );
}

export function WorkTitle({ title }: { title?: string | null }) {
  return (
    <span className="block truncate" title={title || undefined}>
      {title || "—"}
    </span>
  );
}

export function useSelection() {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  return {
    selected,
    toggle(keys: string[], checked: boolean) {
      setSelected((old) => {
        const next = new Set(old);
        keys.forEach((key) => (checked ? next.add(key) : next.delete(key)));
        return next;
      });
    },
    clear() {
      setSelected(new Set());
    },
  };
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
      checked={
        keys.length && keys.every((key) => selected.has(key))
          ? true
          : keys.some((key) => selected.has(key))
            ? "indeterminate"
            : false
      }
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
