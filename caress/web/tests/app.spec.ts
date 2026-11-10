import { test, expect } from "@playwright/test";
const settings = {
  configured: true,
  emby: { url: "http://emby:8096", has_api_key: true, library_ids: ["lib"] },
  translation: {
    endpoint: "https://translator/v1",
    model: "",
    has_api_key: false,
  },
  routes: {
    censored: ["javdb", "javbus"],
    uncensored: ["javdb"],
    fc2: ["fc2"],
    unknown: ["javdb"],
  },
};
const work = {
  id: "1",
  code: "ABC-123",
  title: "Emby 当前标题",
  original_title: "原始标题",
  plot: "Emby 当前简介",
  status: "success",
  needs_enrichment: false,
  people: [],
  media: [{ path: "/strm/ABC-123.strm", filename: "ABC-123.strm" }],
  studios: [],
  genres: [],
  tags: [],
  backdrops: [],
  poster: null,
  emby_url: "/web/index.html#!/item?id=1",
};
test.beforeEach(async ({ page }) => {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    const data =
      path === "/api/settings"
        ? settings
        : path === "/api/settings/libraries"
          ? [{ id: "lib", name: "电影" }]
          : path === "/api/batch"
            ? null
            : path === "/api/works/1"
              ? work
              : path === "/api/works"
                ? { items: [work], total: 1 }
                : {};
    await route.fulfill({ json: data });
  });
});
test("only works and settings; live detail is read-only", async ({ page }) => {
  await page.goto("/");
  await expect(page.getByRole("navigation")).toContainText("作品");
  await expect(page.getByRole("navigation").getByRole("button")).toHaveCount(2);
  await page.getByRole("row", { name: "查看 ABC-123" }).click();
  await expect(
    page.getByRole("heading", { name: "Emby 当前标题" }),
  ).toBeVisible();
  await expect(page.getByText("Emby 当前简介")).toBeVisible();
  await expect(page.getByRole("textbox")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "裁剪" })).toBeVisible();
  await expect(page.getByRole("link", { name: "Emby 中打开" })).toHaveAttribute(
    "href",
    /emby:8096/,
  );
});
test("offline errors do not show stale library data", async ({ page }) => {
  await page.route("**/api/works?**", (route) =>
    route.fulfill({ status: 502, json: { detail: "无法连接 Emby" } }),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("无法连接 Emby");
  await expect(page.getByText("Emby 当前标题")).toHaveCount(0);
});
test("settings use comma-separated routes without operational knobs", async ({
  page,
}) => {
  let saved: Record<string, unknown> | undefined;
  await page.route("**/api/settings", async (route) => {
    if (route.request().method() === "PUT") {
      saved = route.request().postDataJSON();
      await route.fulfill({ json: saved });
    } else await route.fulfill({ json: settings });
  });
  await page.goto("/#settings");
  await expect(page.getByLabel("API Key", { exact: true }).first()).toHaveValue(
    "********",
  );
  const order = await page.locator("legend").allTextContents();
  expect(order.slice(0, 3)).toEqual(["Emby", "翻译", "来源优先级"]);
  await expect(page.getByText("JavDB Cookie")).toHaveCount(0);
  await page.getByLabel("有码", { exact: true }).fill("javbus, javdb");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => saved).toBeDefined();
  expect((saved!.routes as Record<string, string[]>).censored).toEqual([
    "javbus",
    "javdb",
  ]);
  await expect(page.getByRole("status")).toContainText("已保存");
});

test("connect validates unsaved input and reveals libraries in one action", async ({
  page,
}) => {
  let input: unknown;
  await page.route("**/api/settings/libraries", (route) => {
    input = route.request().postDataJSON();
    return route.fulfill({ json: [{ id: "lib", name: "电影" }] });
  });
  await page.goto("/#settings");
  await page.getByLabel("服务器地址").fill("http://new:8096");
  await page.getByRole("button", { name: "连接", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "电影" })).toBeVisible();
  expect((input as typeof settings).emby.url).toBe("http://new:8096");
  await expect(page.getByRole("status")).toHaveCount(0);
});

test("changing Emby input invalidates a pending connection response", async ({
  page,
}) => {
  let release!: () => void;
  const previous = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route("**/api/settings/libraries", async (route) => {
    const input = route.request().postDataJSON() as typeof settings;
    if (input.emby.url === "http://emby:8096") await previous;
    await route
      .fulfill({
        json: [
          {
            id: input.emby.url,
            name: input.emby.url === "http://emby:8096" ? "旧库" : "新库",
          },
        ],
      })
      .catch(() => {});
  });
  await page.goto("/#settings");
  await page.getByLabel("服务器地址").fill("http://new:8096");
  await page.getByRole("button", { name: "连接", exact: true }).click();
  await expect(page.getByRole("checkbox", { name: "新库" })).toBeVisible();
  release();
  await expect(page.getByRole("checkbox", { name: "旧库" })).toHaveCount(0);
});

test("page controls support direct jumps and preserve position after detail", async ({
  page,
}) => {
  const requests: URL[] = [];
  await page.route("**/api/works?**", (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    const number = Math.floor(Number(url.searchParams.get("offset")) / 100) + 1;
    return route.fulfill({
      json: {
        total: 250,
        items: [{ ...work, id: String(number), title: `第 ${number} 页` }],
      },
    });
  });
  await page.route("**/api/works/3", (route) =>
    route.fulfill({ json: { ...work, id: "3" } }),
  );
  await page.goto("/");
  await expect(page.getByRole("table")).toBeVisible();
  await page.getByRole("combobox", { name: "页码", exact: true }).click();
  await page.getByRole("option", { name: "3 / 3", exact: true }).click();
  await expect(
    page.getByRole("cell", { name: "第 3 页", exact: true }),
  ).toBeVisible();
  expect(requests.at(-1)!.searchParams.get("offset")).toBe("200");
  await page.getByRole("row", { name: "查看 ABC-123" }).click();
  await page.getByRole("button", { name: "返回", exact: true }).click();
  await expect(
    page.getByRole("combobox", { name: "页码", exact: true }),
  ).toContainText("3 / 3");
  await page.getByRole("combobox", { name: "每页条数" }).click();
  await page.getByRole("option", { name: "1000 条/页", exact: true }).click();
  await expect
    .poll(() => requests.at(-1)!.searchParams.get("limit"))
    .toBe("1000");
});

test("crop drags at 2:3 and saves Primary without starting a scrape", async ({
  page,
}) => {
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90"><rect width="120" height="90" fill="#37647e"/></svg>';
  const preview = "data:image/svg+xml," + encodeURIComponent(svg);
  let previews = 0,
    applied: number[] | undefined,
    batches = 0,
    sourceReads = 0,
    refreshed = false;
  page.on("request", (request) => {
    if (
      request.method() === "POST" &&
      new URL(request.url()).pathname === "/api/batch"
    )
      batches++;
  });
  await page.route("**/api/works/1/crop/**", async (route) => {
    const url = new URL(route.request().url());
    const path = url.pathname;
    if (path.endsWith("/source")) {
      sourceReads++;
      refreshed = url.searchParams.get("fetch_source") === "true";
    }
    if (path.endsWith("/image")) {
      await route.fulfill({ contentType: "image/svg+xml", body: svg });
      return;
    }
    const input = route.request().postDataJSON() as { box?: number[] } | null;
    if (path.endsWith("/apply")) {
      applied = input?.box;
      await route.fulfill({ json: work });
      return;
    }
    if (path.endsWith("/preview")) previews++;
    await route.fulfill({
      json: {
        token: "session",
        box: input?.box || [20, 0, 60, 90],
        width: 120,
        height: 90,
        preview,
      },
    });
  });
  await page.goto("/#library/work/1");
  await page.getByRole("button", { name: "裁剪", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("img", { name: "裁剪原图" })).toBeVisible();
  await dialog.getByRole("button", { name: "重置选框" }).click();
  await expect.poll(() => previews).toBe(1);
  const selection = dialog.locator(".ReactCrop__crop-selection");
  const bounds = await selection.boundingBox();
  if (!bounds) throw new Error("Crop selection missing");
  await page.mouse.move(
    bounds.x + bounds.width / 2,
    bounds.y + bounds.height / 2,
  );
  await page.mouse.down();
  await page.mouse.move(
    bounds.x + bounds.width / 2 + 10,
    bounds.y + bounds.height / 2,
    { steps: 5 },
  );
  await page.mouse.up();
  await expect.poll(() => previews).toBeGreaterThan(1);
  await dialog.getByRole("button", { name: "更多操作" }).click();
  await page.getByRole("menuitem", { name: "从来源获取原图" }).click();
  await expect.poll(() => sourceReads).toBe(2);
  expect(refreshed).toBe(true);
  await page.setViewportSize({ width: 375, height: 812 });
  await expect
    .poll(async () => {
      const auto = await dialog
        .getByRole("button", { name: "自动裁剪" })
        .boundingBox();
      const original = await dialog
        .getByRole("button", { name: "更多操作" })
        .boundingBox();
      return Math.abs(auto!.y - original!.y);
    })
    .toBeLessThan(1);
  const frame = await dialog.boundingBox();
  expect(frame!.x + frame!.width).toBeLessThanOrEqual(375);
  await dialog.getByRole("button", { name: "保存", exact: true }).click();
  await expect(dialog).toHaveCount(0);
  expect(applied).toBeDefined();
  expect(applied![2] * 3).toBe(applied![3] * 2);
  expect(batches).toBe(0);
});

test("works remain usable at mobile width", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await expect(page.getByRole("row", { name: "查看 ABC-123" })).toBeVisible();
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= window.innerWidth,
    ),
  ).toBe(true);
  await expect(page.getByRole("group", { name: "查看方式" })).toHaveCount(0);
  await expect(
    page.getByRole("searchbox", { name: "搜索番号或标题" }),
  ).toBeVisible();
});

test("settings align with works and configured secrets never submit the mask", async ({
  page,
}) => {
  let body: Record<string, unknown> | undefined;
  await page.route("**/api/settings", (route) => {
    if (route.request().method() === "PUT") {
      body = route.request().postDataJSON();
      return route.fulfill({ json: body });
    }
    return route.fulfill({ json: settings });
  });
  await page.goto("/");
  await page.getByRole("table").waitFor();
  const table = await page.locator("[data-slot=table-container]").boundingBox();
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "设置" })
    .click();
  const key = page.getByLabel("API Key", { exact: true }).first();
  await expect(key).toHaveValue("********");
  await key.focus();
  await expect(key).toHaveValue("");
  await page.getByLabel("模型", { exact: true }).focus();
  await expect(key).toHaveValue("********");
  const form = await page.locator("form").boundingBox();
  expect(Math.abs(form!.width - table!.width)).toBeLessThan(3);
  await expect(page.locator("legend")).toHaveText([
    "Emby",
    "翻译",
    "来源优先级",
  ]);
  await expect(page.locator("details")).toHaveCount(0);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => body).toBeDefined();
  expect(Object.keys(body!).sort()).toEqual([
    "configured",
    "emby",
    "routes",
    "translation",
  ]);
  expect((body!.emby as Record<string, unknown>).api_key).toBeUndefined();
  expect(JSON.stringify(body)).not.toContain("********");
});

test("start button is stable when mode changes and grid is absent", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("row", { name: "查看 ABC-123" }).waitFor();
  await page.getByRole("combobox", { name: "刮削操作" }).click();
  await page
    .getByRole("option", { name: "替换所有的元数据", exact: true })
    .click();
  await expect(
    page.getByRole("button", { name: "开始执行", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "替换所有的元数据", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "大图", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "刷新作品列表" }),
  ).toContainText("刷新");
  await expect(
    page
      .getByRole("row", { name: "查看 ABC-123" })
      .locator("[data-slot=badge]"),
  ).toHaveText("已完成");
});

test("detail next and previous follow current list and return has no icon", async ({
  page,
}) => {
  await page.route("**/api/works/*", (route) => {
    const url = new URL(route.request().url());
    const id = url.pathname.split("/").at(-1)!;
    return route.fulfill({
      json: {
        ...work,
        id,
        code: `ABC-${id}`,
        title: `作品 ${id}`,
        navigation: {
          previous:
            id === "1" ? null : { id: "1", label: "ABC-1", position: 0 },
          next: id === "1" ? { id: "2", label: "ABC-2", position: 1 } : null,
        },
      },
    });
  });
  await page.route("**/api/works?**", (route) =>
    route.fulfill({
      json: {
        items: [{ ...work, id: "1", code: "ABC-1" }],
        total: 2,
        order: [
          { id: "1", label: "ABC-1", position: 0 },
          { id: "2", label: "ABC-2", position: 1 },
        ],
      },
    }),
  );
  await page.goto("/");
  await page.getByRole("row", { name: "查看 ABC-1" }).click();
  await expect(
    page.getByRole("heading", { name: "作品 1", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "上一个作品" })).toBeDisabled();
  await expect(
    page.getByRole("button", { name: "返回", exact: true }).locator("svg"),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "下一个作品" }).click();
  await expect(
    page.getByRole("heading", { name: "作品 2", exact: true }),
  ).toBeVisible();
  await expect(page.getByRole("button", { name: "下一个作品" })).toBeDisabled();
  await page.getByRole("button", { name: "上一个作品" }).click();
  await expect(
    page.getByRole("heading", { name: "作品 1", exact: true }),
  ).toBeVisible();
  await expect(page.getByText("最近成功", { exact: true })).toHaveCount(0);
  await expect(page.getByText(/Item \d/)).toHaveCount(0);
});

test("portraits reserve space and gallery supports buttons and keyboard", async ({
  page,
}) => {
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#546575"/></svg>';
  await page.route("**/fixture-*", (route) =>
    route.fulfill({ contentType: "image/svg+xml", body: svg }),
  );
  await page.route("**/api/works/1", (route) =>
    route.fulfill({
      json: {
        ...work,
        poster: "/fixture-poster",
        backdrops: ["/fixture-cover", "/fixture-still"],
        people: [
          {
            id: "a",
            name: "有头像",
            type: "Actor",
            image: "/fixture-portrait",
          },
          { id: "b", name: "无头像", type: "Actor", image: null },
        ],
      },
    }),
  );
  await page.goto("/#library/work/1");
  await expect(page.getByRole("img", { name: "暂无头像" })).toBeVisible();
  const nameA = await page.getByText("有头像", { exact: true }).boundingBox(),
    nameB = await page.getByText("无头像", { exact: true }).boundingBox();
  expect(Math.abs(nameA!.y - nameB!.y)).toBeLessThan(1);
  await page.getByRole("button", { name: "查看图片 1" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("2 / 3", { exact: true })).toBeVisible();
  const full = await dialog.boundingBox();
  expect(full!.x).toBe(0);
  expect(full!.y).toBe(0);
  expect(full!.width).toBe(page.viewportSize()!.width);
  expect(full!.height).toBe(page.viewportSize()!.height);
  const close = dialog.getByRole("button", { name: "关闭", exact: true });
  const closeBox = await close.boundingBox();
  expect(closeBox!.width).toBeGreaterThanOrEqual(36);
  expect(closeBox!.height).toBeGreaterThanOrEqual(36);
  await page.evaluate(() =>
    window.dispatchEvent(
      new KeyboardEvent("keydown", { key: "ArrowLeft", bubbles: true }),
    ),
  );
  await expect(dialog.getByText("2 / 3", { exact: true })).toBeVisible();
  await close.focus();
  await page.keyboard.press("ArrowLeft");
  await expect(dialog.getByText("2 / 3", { exact: true })).toBeVisible();
  await dialog.getByRole("button", { name: "下一张" }).click();
  await expect(dialog.getByText("3 / 3", { exact: true })).toBeVisible();
  await expect(dialog.getByRole("button", { name: "下一张" })).toBeDisabled();
  await page.keyboard.press("ArrowLeft");
  await expect(dialog.getByText("2 / 3", { exact: true })).toBeVisible();
  await page.setViewportSize({ width: 375, height: 812 });
  expect((await dialog.boundingBox())!.width).toBe(375);
  expect((await dialog.boundingBox())!.height).toBe(812);
  await page.evaluate(() => {
    const target = document.querySelector('[aria-label="图片浏览"]')!;
    const point = (x: number) =>
      new Touch({ identifier: 1, target, clientX: x, clientY: 150 });
    target.dispatchEvent(
      new TouchEvent("touchstart", { touches: [point(260)], bubbles: true }),
    );
    target.dispatchEvent(
      new TouchEvent("touchend", {
        changedTouches: [point(80)],
        bubbles: true,
      }),
    );
  });
  await expect(dialog.getByText("3 / 3", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(page.getByRole("list", { name: "媒体文件" })).toBeVisible();
});

for (const [status, action] of Object.entries({
  pending: "刮削缺少的元数据",
  failed: "刮削缺少的元数据",
  cancelled: "刮削缺少的元数据",
  success: "替换元数据",
  rescrape_failed: "刮削缺少的元数据",
  unsupported: "",
})) {
  test(`detail ${status} has one primary action and preserves confirmation`, async ({
    page,
  }) => {
    await page.route("**/api/works/1", (route) =>
      route.fulfill({
        json: {
          ...work,
          status,
          needs_enrichment: status !== "success",
          last_result: status === "rescrape_failed" ? "failed" : status,
          succeeded_at: ["success", "rescrape_failed"].includes(status)
            ? "2026-11-09"
            : null,
        },
      }),
    );
    await page.goto("/#library/work/1");
    await expect(
      page.getByRole("heading", { name: work.title, exact: true }),
    ).toBeVisible();
    const primary = page.getByRole("button", {
      name: /^(刮削缺少的元数据|替换元数据)$/,
    });
    await expect(primary).toHaveCount(action ? 1 : 0);
    if (action) {
      await expect(primary).toHaveText(action);
      if (action === "替换元数据") {
        await primary.click();
        await expect(page.getByRole("dialog")).toContainText("可能覆盖");
        await page.getByRole("button", { name: "取消", exact: true }).click();
      }
    }
    await expect(page.getByRole("list", { name: "媒体文件" })).toContainText(
      "/strm/ABC-123.strm",
    );
  });
}

test("source is always visible and can return to automatic", async ({
  page,
}) => {
  await page.goto("/#library/work/1");
  const select = page.getByRole("combobox", { name: "刮削来源" });
  await expect(select).toHaveText("自动选择来源");
  await select.click();
  await page.getByRole("option", { name: "javdb", exact: true }).click();
  await expect(select).toHaveText("javdb");
  await select.click();
  await page.getByRole("option", { name: "自动选择来源", exact: true }).click();
  await expect(select).toHaveText("自动选择来源");
  await expect(
    page.getByRole("button", { name: "刷新作品", exact: true }),
  ).toHaveCount(0);
});

test("running batch exposes progress and cancellation", async ({ page }) => {
  let cancelled = false;
  await page.route("**/api/batch", (route) =>
    route.fulfill({
      json: {
        id: "batch",
        status: cancelled ? "cancelled" : "running",
        items: [
          {
            id: "1",
            code: "ABC-123",
            status: cancelled ? "cancelled" : "running",
          },
        ],
        current: cancelled ? [] : ["1"],
      },
    }),
  );
  await page.route("**/api/batch/cancel", (route) => {
    cancelled = true;
    return route.fulfill({
      json: {
        id: "batch",
        status: "cancelled",
        items: [
          { id: "1", status: "cancelled", error: "已取消；尚未开始写入" },
        ],
        current: [],
      },
    });
  });
  await page.goto("/");
  await expect(
    page.getByRole("progressbar", { name: "批次进度" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "取消", exact: true }),
  ).toHaveCount(0);
  expect(cancelled).toBe(true);
});

test("completed batch is a compact summary with optional errors", async ({
  page,
}) => {
  await page.route("**/api/batch", (route) =>
    route.fulfill({
      json: {
        id: "batch",
        status: "finished",
        current: [],
        items: [
          { id: "1", status: "success" },
          { id: "2", status: "failed", error: "来源暂不可用" },
          { id: "3", status: "skipped" },
        ],
      },
    }),
  );
  await page.goto("/");
  const batch = page.getByRole("region", { name: "当前批次" });
  await expect(batch).toContainText("已结束 · 1 成功 · 1 失败 · 1 跳过");
  await expect(batch.getByRole("progressbar")).toHaveCount(0);
  await expect(
    batch.getByText("来源暂不可用", { exact: false }),
  ).not.toBeVisible();
  await batch.locator("summary").click();
  await expect(batch).toContainText("来源暂不可用");
});

test("initial failures can retry without stale content", async ({ page }) => {
  let fails = true,
    bootFails = true;
  await page.route("**/api/settings", (route) =>
    route.fulfill(
      bootFails
        ? { status: 503, json: { detail: "服务暂不可用" } }
        : { json: settings },
    ),
  );
  await page.route("**/api/works/1", (route) =>
    route.fulfill(
      fails ? { status: 502, json: { detail: "连接失败" } } : { json: work },
    ),
  );
  await page.goto("/");
  await expect(page.getByRole("alert")).toContainText("服务暂不可用");
  bootFails = false;
  await page.getByRole("button", { name: "重试", exact: true }).click();
  await page.getByRole("table").waitFor();
  await page.goto("/#library/work/1");
  await expect(page.getByRole("alert")).toContainText("连接失败");
  fails = false;
  await page.getByRole("button", { name: "重试加载", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: work.title, exact: true }),
  ).toBeVisible();
});

test("key layouts keep aligned controls and contain long content", async ({
  page,
}) => {
  await page.route("**/api/works/1", (route) =>
    route.fulfill({
      json: {
        ...work,
        title: "很长的标题 ".repeat(20),
        original_title: "UnbrokenOriginalTitle".repeat(15),
        studios: ["LongStudioName".repeat(10)],
        media: [
          {
            ...work.media[0],
            path: "/mnt/strm/" + "long-folder/".repeat(12) + "ABC-123.strm",
          },
        ],
      },
    }),
  );
  for (const width of [1440, 375]) {
    await page.setViewportSize({ width, height: 900 });
    await page.goto("/");
    await page.getByRole("table").waitFor();
    await expect(page.getByPlaceholder("搜索", { exact: true })).toBeVisible();
    const search = await page
      .getByPlaceholder("搜索", { exact: true })
      .boundingBox();
    const filter = await page
      .getByRole("combobox", { name: "筛选状态" })
      .boundingBox();
    expect(Math.abs(search!.height - filter!.height)).toBeLessThan(1);
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    await page.goto("/#settings");
    await page.getByRole("button", { name: "保存", exact: true }).waitFor();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
    const size = await page
      .getByLabel("服务器地址")
      .evaluate((e) => Number.parseFloat(getComputedStyle(e).fontSize));
    expect(size).toBeGreaterThanOrEqual(14);
    await page.goto("/#library/work/1");
    await page.getByRole("list", { name: "媒体文件" }).waitFor();
    expect(
      await page.evaluate(
        () => document.documentElement.scrollWidth <= innerWidth,
      ),
    ).toBe(true);
  }
});

test("image retry bypasses failed cache and a new URL starts cleanly", async ({
  page,
}) => {
  await page.addInitScript(() => {
    Object.defineProperty(crypto, "randomUUID", { value: undefined });
  });
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="300"><rect width="200" height="300" fill="#66737d"/></svg>';
  let tag = "old";
  const requests: URL[] = [];
  await page.route("**/api/works/1", (route) =>
    route.fulfill({
      json: { ...work, poster: `/api/images/1/Primary/0?tag=${tag}` },
    }),
  );
  await page.route("**/api/images/**", (route) => {
    const url = new URL(route.request().url());
    requests.push(url);
    return url.searchParams.has("retry") ||
      url.searchParams.get("tag") === "new"
      ? route.fulfill({ contentType: "image/svg+xml", body: svg })
      : route.fulfill({
          contentType: "image/png",
          headers: { "Cache-Control": "max-age=3600" },
          body: "invalid image",
        });
  });
  await page.goto("/#library/work/1");
  await page.getByRole("button", { name: "查看封面" }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("button", { name: "重试", exact: true }).click();
  await expect(
    dialog.getByRole("img", { name: "封面", exact: true }),
  ).toBeVisible();
  expect(
    requests.some(
      (url) =>
        url.searchParams.get("tag") === "old" && url.searchParams.has("retry"),
    ),
  ).toBe(true);
  await page.keyboard.press("Escape");
  tag = "new";
  await page.getByRole("button", { name: "返回", exact: true }).click();
  await page.getByRole("row", { name: "查看 ABC-123" }).click();
  await expect(
    page.getByRole("img", { name: "封面", exact: true }),
  ).toBeVisible();
  expect(requests.at(-1)!.searchParams.get("tag")).toBe("new");
  expect(requests.at(-1)!.searchParams.has("retry")).toBe(false);
});

test("work count hierarchy and invalid source order", async ({ page }) => {
  await page.goto("/");
  const count = page.getByText("1 部作品", { exact: true });
  await expect(count).toBeVisible();
  expect(await count.evaluate((e) => getComputedStyle(e).fontSize)).toBe(
    "16px",
  );
  await page.getByRole("checkbox", { name: "选择本页" }).click();
  const selected = page.getByText(" · 已选 1", { exact: true });
  expect(await selected.evaluate((e) => getComputedStyle(e).fontSize)).toBe(
    "14px",
  );
  await page
    .getByRole("navigation")
    .getByRole("button", { name: "设置" })
    .click();
  let saves = 0;
  await page.route("**/api/settings", (route) => {
    saves++;
    return route.fulfill({ json: settings });
  });
  await page.getByLabel("有码", { exact: true }).fill("avsox");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("AVSOX 不支持有码作品");
  await page.getByLabel("有码", { exact: true }).fill("dmm,dmm");
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("有码来源无效");
  expect(saves).toBe(0);
});

for (const [status, missing, expected] of [
  ["success", true, "scrape"],
  ["success", false, "force"],
  ["failed", false, "scrape"],
] as const) {
  test(`detail ${status} missing=${missing} executes ${expected} and reloads`, async ({
    page,
  }) => {
    let body: Record<string, unknown> | undefined;
    let changed = false;
    await page.route("**/api/works/1", (route) =>
      route.fulfill({
        json: {
          ...work,
          status,
          needs_enrichment: missing,
          title: changed ? "最新数据" : work.title,
        },
      }),
    );
    await page.route("**/api/batch", (route) => {
      if (route.request().method() !== "POST")
        return route.fulfill({ json: null });
      body = route.request().postDataJSON();
      changed = true;
      return route.fulfill({
        json: {
          id: "b",
          status: "finished",
          items: [{ id: "1", status: "success" }],
          current: [],
        },
      });
    });
    await page.goto("/#library/work/1");
    await page.getByRole("combobox", { name: "刮削来源" }).click();
    await page.getByRole("option", { name: "javdb", exact: true }).click();
    await page
      .getByRole("button", {
        name: expected === "force" ? "替换元数据" : "刮削缺少的元数据",
        exact: true,
      })
      .click();
    if (expected === "force")
      await page.getByRole("button", { name: "确认", exact: true }).click();
    await expect.poll(() => body).toBeDefined();
    expect(body).toMatchObject({
      action: expected,
      source: "javdb",
      confirmed: expected === "force",
    });
    await expect(
      page.getByRole("heading", { name: "最新数据", exact: true }),
    ).toBeVisible();
  });
}

test("settings automatically restores libraries; failed reload preserves selection", async ({
  page,
}) => {
  let failure = false;
  let saved: Record<string, unknown> | undefined;
  await page.route("**/api/settings/libraries", (route) =>
    route.fulfill(
      failure
        ? { status: 502, json: { detail: "连接失败" } }
        : {
            json: [
              { id: "lib", name: "电影" },
              { id: "other", name: "其他" },
            ],
          },
    ),
  );
  await page.route("**/api/settings", (route) => {
    if (route.request().method() === "PUT") {
      saved = route.request().postDataJSON();
      return route.fulfill({ json: saved });
    }
    return route.fulfill({ json: settings });
  });
  await page.goto("/#settings");
  await expect(
    page.getByRole("checkbox", { name: "电影", exact: true }),
  ).toBeChecked();
  await expect(
    page.getByRole("checkbox", { name: "其他", exact: true }),
  ).not.toBeChecked();
  failure = true;
  await page.getByRole("button", { name: "连接", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("连接失败");
  await expect(
    page.getByRole("checkbox", { name: "电影", exact: true }),
  ).toBeChecked();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => saved).toBeDefined();
  expect((saved!.emby as typeof settings.emby).library_ids).toEqual(["lib"]);
  await expect(page.getByText("按顺序查询，命中即停止")).toHaveCount(0);
});

test("crop failure offers source recovery without a permanent fetch button", async ({
  page,
}) => {
  const svg =
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="90"><rect width="120" height="90" fill="#657581"/></svg>';
  let fetched = false;
  await page.route("**/api/works/1/crop/**", (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/image"))
      return route.fulfill({ contentType: "image/svg+xml", body: svg });
    if (url.pathname.endsWith("/source")) {
      fetched = url.searchParams.get("fetch_source") === "true";
      if (!fetched)
        return route.fulfill({
          status: 502,
          json: { detail: "无法获取完整原图" },
        });
    }
    return route.fulfill({
      json: {
        token: "new",
        width: 120,
        height: 90,
        box: [20, 0, 60, 90],
        preview: "data:image/svg+xml," + encodeURIComponent(svg),
      },
    });
  });
  await page.goto("/#library/work/1");
  await page.getByRole("button", { name: "裁剪", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByRole("alert")).toHaveText("无法获取完整原图");
  await dialog
    .getByRole("button", { name: "从来源获取原图", exact: true })
    .click();
  await expect(
    dialog.getByRole("img", { name: "裁剪原图", exact: true }),
  ).toBeVisible();
  expect(fetched).toBe(true);
  await expect(
    dialog.getByRole("button", { name: "从来源获取原图", exact: true }),
  ).toHaveCount(0);
});
