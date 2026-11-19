import { useRef } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from "@/components/ui/dialog";

export interface Confirmation {
  resolve: (answer: boolean) => void;
}

export function ConfirmationDialog({
  value,
  close,
}: {
  value: Confirmation | null;
  close: (answer: boolean) => void;
}) {
  const cancel = useRef<HTMLButtonElement>(null);
  return (
    <Dialog
      open={!!value}
      onOpenChange={(open) => {
        if (!open) close(false);
      }}
    >
      <DialogContent
        initialFocus={cancel}
        showCloseButton={false}
        className="max-h-[85dvh] overflow-y-auto"
      >
        <DialogHeader className="pr-0 text-left">
          <DialogTitle>替换所有的元数据？</DialogTitle>
          <DialogDescription>
            将重新获取并替换元数据、封面、背景图和演员头像。来源缺失的元数据字段将被清空。没有新封面或头像时保留原图；来源没有背景图时清空现有背景图。写入失败时，部分更改可能已保存到 Emby。
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="flex-row justify-end">
          <Button ref={cancel} variant="outline" onClick={() => close(false)}>
            取消
          </Button>
          <Button onClick={() => close(true)}>替换</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
