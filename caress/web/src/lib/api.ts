import { validationText } from "./messages";
export async function api<T>(
  path: string,
  method = "GET",
  data?: unknown,
  signal?: AbortSignal,
): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`/api/${path}`, {
      method,
      signal,
      headers:
        data === undefined ? undefined : { "Content-Type": "application/json" },
      body: data === undefined ? undefined : JSON.stringify(data),
    });
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error("无法连接 Caress，请检查网络连接");
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error(
      response.ok
        ? "服务返回的数据无效"
        : `服务暂不可用（HTTP ${response.status}）`,
    );
  }
  if (!response.ok) {
    const detail = (value as { detail?: unknown } | null)?.detail;
    throw new Error(
      detail === undefined
        ? `请求失败（HTTP ${response.status}）`
        : validationText(detail),
    );
  }
  return value as T;
}
