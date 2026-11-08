import { test, expect } from "@playwright/test";

test.beforeEach(async ({ page }) => { await page.goto("/__components"); });

test("controlled dialog traps focus, Escape closes and restores focus", async ({ page }) => {
  const trigger = page.getByRole("button", { name: "打开受控弹窗", exact: true });
  await trigger.click();
  const dialog = page.getByRole("dialog", { name: "受控弹窗", exact: true });
  await expect(dialog).toBeVisible();
  await page.keyboard.press("Shift+Tab");
  await expect(dialog.locator(":focus")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(trigger).toBeFocused();
});

test("nested dialog closes independently and returns focus to inner trigger", async ({ page }) => {
  await page.getByRole("button", { name: "打开受控弹窗", exact: true }).click();
  const trigger = page.getByRole("button", { name: "打开嵌套弹窗" });
  await trigger.click();
  await expect(page.getByRole("dialog", { name: "嵌套弹窗", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "关闭内层" }).click();
  await expect(page.getByRole("dialog", { name: "嵌套弹窗", exact: true })).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "受控弹窗", exact: true })).toBeVisible();
  await expect(trigger).toBeFocused();
});

test("select keyboard navigation commits label and closes only the popup", async ({ page }) => {
  await page.getByRole("button", { name: "打开受控弹窗", exact: true }).click();
  const trigger = page.getByRole("combobox", { name: "受控选择", exact: true });
  await trigger.focus();
  await page.keyboard.press("ArrowDown");
  await expect(page.getByRole("option", { name: "第一项", exact: true })).toBeFocused();
  await page.keyboard.press("End");
  await expect(page.getByRole("option", { name: "第二项", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(trigger).toHaveText("第二项");
  await expect(page.getByRole("listbox")).toHaveCount(0);
  await expect(page.getByRole("dialog", { name: "受控弹窗", exact: true })).toBeVisible();
  await expect(trigger).toBeFocused();
});

test("uncontrolled dialog and select respond to pointer and Escape", async ({ page }) => {
  await page.getByRole("button", { name: "打开非受控弹窗" }).click();
  await expect(page.getByRole("dialog", { name: "非受控弹窗" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const trigger = page.getByRole("combobox", { name: "非受控选择" });
  await trigger.click();
  await page.getByRole("option", { name: "第二项" }).click();
  await expect(trigger).toHaveText("第二项");
});

test("checkbox controlled, uncontrolled and indeterminate semantics", async ({ page }) => {
  const controlled = page.getByRole("checkbox", { name: "受控复选", exact: true });
  await controlled.focus();
  await page.keyboard.press("Space");
  await expect(controlled).toBeChecked();
  const uncontrolled = page.getByRole("checkbox", { name: "非受控复选" });
  await expect(uncontrolled).toBeChecked();
  await uncontrolled.click();
  await expect(uncontrolled).not.toBeChecked();
  await expect(page.getByRole("checkbox", { name: "部分选择" })).toHaveAttribute("aria-checked", "mixed");
});
