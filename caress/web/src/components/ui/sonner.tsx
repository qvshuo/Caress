import { Toaster as Sonner } from "sonner";

export { toast } from "sonner";

export function Toaster() {
  return <Sonner theme="system" closeButton toastOptions={{
    classNames: { toast: "border-border bg-popover text-popover-foreground shadow-md", description: "text-muted-foreground" },
  }} />;
}
