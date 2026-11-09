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
    throw new Error("无法连接服务，请检查网络。");
  }
  let value: unknown;
  try {
    value = await response.json();
  } catch (error) {
    if (error instanceof Error && error.name === "AbortError") throw error;
    throw new Error(
      response.ok
        ? "服务响应格式异常，请重试。"
        : `服务暂不可用（${response.status}）`,
    );
  }
  if (!response.ok) {
    const detail = (value as { detail?: unknown } | null)?.detail;
    throw new Error(
      detail === undefined
        ? `服务请求失败（${response.status}）`
        : validationText(detail),
    );
  }
  return value as T;
}
