import type { ReactNode } from "react";
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
          <DialogDescription
            render={<div />}
            className="whitespace-pre-wrap break-words"
          >
            {value?.description}
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
