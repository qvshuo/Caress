import { useEffect, useState } from "react";
import { FolderOpen, ArrowUp } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { api } from "@/lib/api";
import type { DirectoryView } from "@/lib/types";
import { Empty } from "./shared";

export default function DirectoryBrowser({
  kind,
  initial,
  choose,
  close,
}: {
  kind: string;
  initial: string;
  choose: (path: string) => void;
  close: () => void;
}) {
  const [path, setPath] = useState(initial);
  const [target, setTarget] = useState({ path: initial });
  const [view, setView] = useState<DirectoryView | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError("");
    setView(null);
    void api<DirectoryView>(
      `directories?kind=${kind}${target.path ? `&path=${encodeURIComponent(target.path)}` : ""}`,
      "GET",
      undefined,
      controller.signal,
    )
      .then((value) => {
        if (controller.signal.aborted) return;
        setView(value);
        setPath(value.path ?? "");
      })
      .catch((e: unknown) => {
        if (!controller.signal.aborted)
          setError(e instanceof Error ? e.message : "无法访问文件夹。");
      })
      .finally(() => {
        if (!controller.signal.aborted) setLoading(false);
      });
    return () => controller.abort();
  }, [kind, target]);
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open) close();
      }}
    >
      <DialogContent aria-describedby={undefined}>
        <DialogHeader>
          <DialogTitle>选择文件夹</DialogTitle>
        </DialogHeader>
        <div className="flex gap-2">
          <Button
            variant="outline"
            size="icon"
            aria-label="上级目录"
            disabled={loading || !view?.path}
            onClick={() => setTarget({ path: view?.parent ?? "" })}
          >
            <ArrowUp />
          </Button>
          <Input
            aria-label="目录路径"
            disabled={loading}
            value={path}
            onChange={(e) => setPath(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                e.preventDefault();
                setTarget({ path });
              }
            }}
          />
        </div>
        <div className="max-h-64 overflow-y-auto rounded-md border">
          {loading ? (
            <Empty>正在读取…</Empty>
          ) : error ? (
            <Empty>目录读取失败</Empty>
          ) : !view?.directories.length ? (
            <Empty>暂无文件夹</Empty>
          ) : (
            view?.directories.map((d) => (
              <Button
                variant="ghost"
                className="w-full justify-start rounded-none focus-visible:ring-inset"
                key={d.path}
                onClick={() => setTarget({ path: d.path })}
              >
                <FolderOpen />
                <span className="truncate">{d.name}</span>
              </Button>
            ))
          )}
        </div>
        {error && (
          <p role="alert" className="text-sm text-destructive">
            {error}
          </p>
        )}
        {view?.path !== path && !loading && <p className="text-sm text-muted-foreground">输入路径后按回车加载</p>}
        <p className="break-all text-sm text-muted-foreground">
          已选目录：{view?.path ?? "无"}
        </p>
        <DialogFooter>
          <Button variant="outline" onClick={close}>
            取消
          </Button>
          <Button
            disabled={loading || !!error || !view?.path || path !== view.path}
            onClick={() => {
              if (view?.path) choose(view.path);
            }}
          >
            确认
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
