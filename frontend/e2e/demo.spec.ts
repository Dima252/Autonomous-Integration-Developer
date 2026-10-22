import { expect, test, type Page } from "@playwright/test";

// The public demo: static files, no backend (ADR 017).

const isPhone = (page: Page) => (page.viewportSize()?.width ?? 1280) < 1024;

/** Small screens show one pane at a time. */
async function showPane(page: Page, pane: "Chat" | "Workspace") {
  if (isPhone(page)) await page.getByRole("navigation", { name: "Panes" }).getByRole("button", { name: pane }).click();
}

test("the sample run plays to the end", async ({ page }) => {
  await page.goto("/");
  await showPane(page, "Workspace");
  const workspace = page.getByRole("region", { name: "Run workspace" });
  await expect(workspace.getByText("Scripted sample")).toBeVisible();

  // It starts by itself; 4× gets through the ~42 s script in ~11 s.
  await expect(page.getByRole("button", { name: "Pause" })).toBeVisible();
  await page.getByRole("radiogroup", { name: "Playback speed" }).getByRole("radio", { name: "4×" }).click();
  await expect(workspace.getByText("Completed", { exact: true })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Play" })).toBeVisible();

  // The run's results stay inspectable afterwards.
  await page.getByRole("tab", { name: /Checks/ }).click();
  await expect(page.getByRole("tabpanel")).not.toBeEmpty();
});

test("Live says it is offline instead of failing", async ({ page }) => {
  await page.goto("/");
  await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /Live/ }).click();
  await showPane(page, "Chat");

  const chat = page.getByRole("region", { name: "Chat" });
  await expect(chat.getByRole("status")).toContainText("Live mode is offline");
  await expect(chat.getByRole("textbox", { name: "Message" })).toHaveCount(0);
  await expect(chat.getByText("Can't reach the API")).toHaveCount(0);

  // The way out goes back to the sample run.
  await expect(chat.getByRole("button", { name: "Watch the sample run" })).toHaveCount(1);
  await chat.getByRole("button", { name: "Watch the sample run" }).click();
  await expect(page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /Sample/ })).toHaveAttribute(
    "aria-checked",
    "true",
  );
});

test("the command palette offers no live examples while offline", async ({ page }) => {
  test.skip(isPhone(page), "the palette is a keyboard feature");
  await page.goto("/");
  // Wait for the health check to settle: the sample footer drops "Try it live".
  await expect(page.getByText("Live mode runs on demand")).toBeVisible();
  await page.keyboard.press("Control+k");
  const palette = page.getByRole("dialog", { name: "Commands" });
  await expect(palette.getByText("Play the sample run from the start")).toBeVisible();
  await expect(palette.getByText("Ask (live)")).toHaveCount(0);
});
