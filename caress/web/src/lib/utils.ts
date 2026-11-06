import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export const active = (value: { status: string }) =>
  ["pending", "running"].includes(value.status);
export const valid = (value: { status: string }) =>
  !["invalid_key", "conflict"].includes(value.status);
export function formatDate(value: string, progress = false): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  const pad = (n: number) => String(n).padStart(2, "0");
  const day = progress
    ? `${date.getFullYear()}/${pad(date.getMonth() + 1)}/${pad(date.getDate())}`
    : `${date.getFullYear()}年${date.getMonth() + 1}月${date.getDate()}日`;
  return `${day} ${date.toLocaleTimeString("zh-CN", { hour12: false })}`;
}
