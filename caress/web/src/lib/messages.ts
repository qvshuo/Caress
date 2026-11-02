import { fields } from "./metadata";

const errors: Record<string, string> = {
  "cannot change configuration while metadata is being edited": "保存完成后可修改设置。",
  "cannot create tasks while metadata is being edited": "保存完成后可创建任务。",
  "cannot scan while a task is active": "任务结束后可扫描作品。",
  "task overlaps an active write task": "所选作品已有任务。",
  "no eligible works or requested directory is invalid/conflicting": "请选择名称正确的作品。",
  "cannot retry active task": "任务尚未结束。",
  "task contains no failed items": "没有可重试的未完成项。",
  "cancel the task before deleting it": "请先取消任务，再删除记录。",
  "task is not active": "任务已经结束。",
  "directory is outside the available mounts": "请选择可访问的文件夹。",
  "directory does not exist": "文件夹不存在。",
  "directory is not readable": "无法读取文件夹，请检查权限。",
  "configure media and metadata directories first": "请先设置作品目录和元数据目录。",
  "configuration change in progress": "正在更新设置，请稍后。",
  "filesystem access failed": "无法访问元数据目录。",
  "source image changed; preview again": "原图已更新，请重新预览。",
  "not found": "记录不存在。",
  "work not found": "作品已不在作品库中。",
  "image not found": "图片不存在，请重新选择来源。",
  "source variant unavailable": "来源候选不可用，请重新刮削。",
  "title must not be empty": "标题不能为空。",
  "movie title must not be empty": "标题不能为空。",
  "translation API key or model is not configured": "请填写翻译模型与 API Key。",
  "translation authentication failed": "翻译认证失败，请检查 API Key。",
  "translation returned empty text": "翻译返回空内容。",
  "translation failed after bounded retries": "翻译重试后仍失败。",
  "artwork exceeds 32 MiB": "图片超过 32 MiB。",
  "artwork response is not a supported image": "来源未返回有效图片。",
  "artwork is corrupt": "图片损坏，无法读取。",
  "invalid image dimensions": "图片尺寸无效。",
  "image is too small for a portrait cover": "图片太小，无法裁剪封面。",
  "face detection failed; check the YuNet model": "人脸识别失败，请检查模型。",
  "crop must be an in-bounds 2:3 rectangle": "请在原图范围内选择 2:3 选区。",
  "invalid crop selection": "裁剪选区无效，请重新选择。",
  "invalid movie NFO": "NFO 格式无效，无法读取。",
  "unsafe or oversized NFO": "NFO 文件不安全或过大。",
  "NFO number differs from directory": "NFO 番号与作品目录不一致。",
  "NFO fields violate the Emby contract": "NFO 字段不符合 Emby 要求。",
  "media roots and metadata root must be separate directory trees": "作品目录与元数据目录不能重叠。",
  "cannot update portraits while tasks are active": "任务结束后可更新头像。",
  "invalid gfriends file tree": "头像索引格式已变化。",
  "gfriends request failed": "头像来源请求失败。",
  "only JavDB supports a configured Cookie": "仅 JavDB 可配置 Cookie。",
  "paste only the single-line Cookie value: name=value; name2=value2": "请仅填写单行请求 Cookie。",
  "paste the request Cookie value, not Set-Cookie": "请填写请求 Cookie，不是 Set-Cookie。",
  "source address requires an HTTPS URL without credentials or query": "来源地址须为不含参数的 HTTPS 网址。",
  "translation endpoint requires an HTTP or HTTPS URL": "翻译接口须为有效 HTTP 或 HTTPS 地址。",
  "routes must contain exactly the four catalog categories": "来源顺序须包含全部四种番号类型。",
  "process interrupted": "服务已中断，本次任务未完成。",
  "source denied the exit IP/ASN": "来源拒绝当前出口 IP。",
  "source is unavailable from this exit region": "来源不支持当前出口地区。",
  "source requires an authenticated session": "来源要求登录。",
  "FC2 requires an authenticated session": "FC2 要求登录，未取得元数据。",
  "source verification page remains": "来源仍在显示访问验证页。",
  "AirAV search cards lack catalog numbers or links": "AirAV 结果缺少番号或链接。",
  "source returned HTTP 404": "来源未找到该作品。",
  "FlareSolverr is disabled or client is not open": "访问验证不可用，请检查 FlareSolverr。",
  "CF challenge remains after solver": "访问验证未通过。",
};

export function errorText(value: unknown): string {
  const text = value instanceof Error ? value.message : typeof value === "string" ? value : "操作失败，请重试。";
  if (errors[text]) return errors[text];
  if (text.startsWith("图片保存失败，保留旧图：")) return "图片保存失败，保留旧图：" + errorText(text.slice(12));
  if (text.startsWith("翻译服务不可用：")) return "翻译服务不可用：" + errorText(text.slice(8));
  for (const [prefix, label] of [["DMM metadata API: ", "DMM 元数据接口："], ["DMM web search: ", "DMM 网页搜索："]] as const) {
    if (text.startsWith(prefix)) return label + errorText(text.slice(prefix.length));
  }
  if (text.startsWith("JavBus detail markers missing: ")) {
    const labels: Record<string, string> = { catalog: "番号", title: "标题", cover: "封面" };
    return "JavBus 详情缺少：" + text.slice("JavBus detail markers missing: ".length).split(", ").map((field) => labels[field] ?? field).join("、");
  }
  const http = text.match(/^(translation|gfriends|source returned)?\s*HTTP (\d+)/);
  if (http) return `${http[1] === "translation" ? "翻译服务" : http[1] === "gfriends" ? "头像来源" : "来源"}返回 HTTP ${http[2]}。`;
  if (/Failed to fetch|Load failed|NetworkError|transport failed/.test(text)) return "网络请求失败，请检查连接。";
  if (/schema changed|markers missing|container missing|structured data missing|source document changed|invalid JSON response|returned no data|title missing/.test(text)) return "来源数据结构已变化，暂时无法解析。";
  if (/only (accepts|supports)|does not (belong|resolve)|catalog does not belong/.test(text)) return "来源不支持此番号类型。";
  if (/invalid .* (ID|CID)|expected a .*movie URL/.test(text)) return "来源作品编号或地址无效。";
  if (/differs|different movie ID|different CID|redirected to another work/.test(text)) return "来源返回的作品与番号不一致。";
  if (/^route .* must be nonempty without duplicate sources$/.test(text)) return "来源顺序不能为空或包含重复项。";
  if (text.startsWith("media root is not a directory:")) return "作品目录不存在，请检查路径。";
  return text;
}

export function validationText(detail: unknown): string {
  if (typeof detail === "string") return errorText(detail);
  if (!Array.isArray(detail)) return "提交内容有误，请检查填写项。";
  return detail.map((item: { loc?: unknown[]; msg?: string; type?: string }) => {
    const labels: Record<string, string> = { ...Object.fromEntries(fields.map(([key, label]) => [key, label])), model: "翻译模型", endpoint: "接口地址", api_key: "API Key", routes: "来源顺序", censored: "有码", uncensored: "无码", metadata_directory: "元数据目录", media_directories: "作品目录" };
    const field = item.loc?.filter((part) => part !== "body").map((part) => labels[String(part)] ?? String(part)).join(" · ");
    const reason = item.type === "missing" ? "未填写" : item.msg?.startsWith("Value error, ") ? errorText(item.msg.slice(13)) : "填写不符合要求";
    return `${field ? `${field}：` : ""}${reason}`;
  }).join("；") || "提交内容有误，请检查填写项。";
}
