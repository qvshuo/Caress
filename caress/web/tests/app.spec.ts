import { test, expect, type Page } from "@playwright/test";

const png = "iVBORw0KGgoAAAANSUhEUgAAAHgAAAC0CAIAAADQLH9KAAABnUlEQVR4nO3SQQ3AMAADsaz8OXcoei+bQKRTvu2O906wgdAdj44IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEjggdEToidEToiNARoSNCR4SOCB0ROiJ0ROiI0BGhI0JHhI4IHRE6InRE6IjQEaEjQkeEXuMHY2MCZ2IstesAAAAASUVORK5CYII=";

async function mockApp(page: Page, polling = false) {
  const state = { detailReads: 0, polls: 0, saved: null as Record<string, unknown> | null };
  const metadata = { code: "ABC-123", title: "已保存标题", original_title: "原始标题", plot: "", release_date: null, year: null, actors: [], directors: [], studio: null, genres: [], tags: [] };
  const work = { code: "ABC-123", category: "censored", status: "scraped", title: metadata.title, actors: [], warnings: [], media: [] };
  const settings = { configured: true, pending_settings: false, translation_status: { valid: null, message: "" }, media_directories: ["/mnt/strm"], flaresolverr_url: null, timeout_seconds: 25, retries: 1, retry_delay_seconds: 5, source_timeout_seconds: 180, sites: {}, routes: { censored: ["dmm"], uncensored: ["javdb"], fc2: ["fc2"], unknown: ["javdb"] }, default_routes: {}, source_defaults: {}, source_intervals: {}, emby: { url: "", api_key: "" }, translation: { endpoint: "https://example.invalid", model: "", api_key: "", timeout_seconds: 30, interval_seconds: 1 } };
  const detail = () => ({ ...work, metadata: { ...metadata }, editable: true, variants: [], route_sources: [], selection: {}, artwork: ["ABC-123-poster.png", "ABC-123-fanart.png"], artwork_versions: {}, last_result: null });
  await page.route("**/api/**", async route => {
    const path = new URL(route.request().url()).pathname;
    let body: unknown = {};
    if (path.endsWith("/artwork")) return route.fulfill({ contentType: "image/png", body: Buffer.from(png, "base64") });
    if (path.endsWith("/crop")) body = { box: [0, 0, 120, 180], faces: [], width: 120, height: 180, fingerprint: "test-fingerprint", preview: `data:image/png;base64,${png}` };
    else if (path.endsWith("/settings")) body = settings;
    else if (path.endsWith("/metadata")) {
      state.saved = route.request().postDataJSON() as Record<string, unknown>;
      metadata.title = String(state.saved.title);
      body = detail();
    } else if (path.endsWith("/works/detail")) { state.detailReads++; body = detail(); }
    else if (path.endsWith("/works/updates")) { state.polls++; metadata.title = "后台新标题"; body = { time: new Date().toISOString(), works: [{ ...work, title: metadata.title, progress_at: String(state.polls) }] }; }
    else if (path.endsWith("/works")) body = [work];
    else if (path.endsWith("/tasks")) body = polling ? [{ id: "task", sequence: 1, type: "refresh", status: "running", total: 1, success: 0, failed: 0, skipped: 0, progress: state.polls, created_at: "2026-01-01T00:00:00Z" }] : [];
    return route.fulfill({ json: body });
  });
  await page.goto("/#library/work/ABC-123");
  await expect(page.locator("#metadata-title")).toHaveValue("已保存标题");
  return state;
}

test("unsaved metadata survives task polling and saves the complete form", async ({ page }) => {
  const state = await mockApp(page, true);
  await page.locator("#metadata-title").fill("未保存草稿");
  await expect.poll(() => state.polls).toBeGreaterThan(1);
  await expect(page.locator("#metadata-title")).toHaveValue("未保存草稿");
  expect(state.detailReads).toBeGreaterThan(1);
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => state.saved?.title).toBe("未保存草稿");
  expect(Object.keys(state.saved!)).toEqual(expect.arrayContaining(["title", "original_title", "plot", "release_date", "year", "actors", "directors", "studio", "genres", "tags", "field_inputs", "field_sources", "image_choices"]));
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeDisabled();
});

test("navigation confirmation protects drafts and Cancel restores focus", async ({ page }) => {
  await mockApp(page);
  await page.locator("#metadata-title").fill("未保存草稿");
  const navigation = page.getByRole("navigation", { name: "主导航" }).getByRole("button", { name: "设置", exact: true });
  await navigation.click();
  await expect(page.getByRole("dialog", { name: "离开页面" })).toBeVisible();
  await page.getByRole("button", { name: "取消", exact: true }).click();
  await expect(page.locator("#metadata-title")).toHaveValue("未保存草稿");
  await expect(navigation).toBeFocused();
  await navigation.click();
  await page.getByRole("button", { name: "确认", exact: true }).click();
  await expect(page).toHaveURL(/#settings$/);
});

test("crop editor previews and applies only to the in-memory form", async ({ page }) => {
  const state = await mockApp(page);
  await page.getByRole("button", { name: "调整封面", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "调整封面" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "居中裁剪" }).click();
  await dialog.getByRole("button", { name: "应用裁剪" }).click();
  await expect(dialog).toHaveCount(0);
  expect(state.saved).toBeNull();
  await expect(page.getByRole("button", { name: "保存", exact: true })).toBeEnabled();
  await page.getByRole("button", { name: "保存", exact: true }).click();
  await expect.poll(() => state.saved?.image_choices).toEqual({ poster: { source: "custom", file: "ABC-123-fanart.png", box: [0, 0, 120, 180], fingerprint: "test-fingerprint" } });
});
