export function validationText(detail: unknown): string {
  if (typeof detail === "string") return detail;
  return "请求参数无效，请检查输入";
}
