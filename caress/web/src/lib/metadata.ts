import type { Variant, WorkDetail } from "./types";

export const fields = [
  ["title", "标题", "textarea"],
  ["plot", "剧情", "textarea"],
  ["release_date", "日期", "text"],
  ["runtime_minutes", "时长分钟", "number"],
  ["actors", "演员", "list"],
  ["directors", "导演", "list"],
  ["studio", "制作商", "text"],
  ["series", "系列", "text"],
  ["genres", "类型", "list"],
  ["tags", "标签", "list"],
] as const;
export type FieldKey = (typeof fields)[number][0];
export type Draft = Record<FieldKey, string>;
export function variantMetadata(variant: Variant) {
  return variant.metadata ? { ...variant.metadata, ...variant.processed_fields } : null;
}
export function metadataDraft(detail: WorkDetail): Draft {
  const metadata = detail.metadata;
  return Object.fromEntries(
    fields.map(([key, , type]) => {
      const value = metadata?.[key];
      return [
        key,
        type === "list"
          ? Array.isArray(value)
            ? value
                .map((item) => (typeof item === "string" ? item : item.name))
                .join("\n")
            : ""
          : key === "release_date" && typeof value === "string"
            ? value.replace(/^(\d{4})-0?(\d+)-0?(\d+)$/, "$1年$2月$3日")
            : String(value ?? ""),
      ];
    }),
  ) as Draft;
}
export function metadataForm(draft: Draft): Record<FieldKey, unknown> {
  const changes: Record<string, unknown> = {};
  for (const [key, , type] of fields) {
    const value = draft[key].trim();
    if (type === "list") {
      const names = [
        ...new Set(
          value
            .split("\n")
            .map((s) => s.trim())
            .filter(Boolean),
        ),
      ];
      changes[key] =
        key === "actors"
          ? names.map((name) => ({ name }))
          : names;
    } else if (key === "release_date" && value) {
      const match = value.match(/^(\d{4})年(\d{1,2})月(\d{1,2})日$/);
      if (!match) throw new Error("日期格式：2026年10月11日");
      changes[key] =
        `${match[1]}-${match[2]!.padStart(2, "0")}-${match[3]!.padStart(2, "0")}`;
    } else
      changes[key] =
        type === "number" ? (value ? Number(value) : null) : value || null;
  }
  return changes as Record<FieldKey, unknown>;
}
