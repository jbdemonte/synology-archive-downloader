import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({ locale: "fr-FR" });
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
try {
  const { id } = await (
    await page.request.post(base + "/api/jobs", {
      data: { url: "demo-refresh", paused: true },
    })
  ).json();
  await page.goto(base);
  await page.locator("#refresh-manifest").click();
  await page
    .locator("#refresh-summary")
    .filter({ hasText: "Nouveaux : 1 · Modifiés : 1 · Absents conservés : 1" })
    .waitFor();
  await page.locator("#refresh-select").click();
  await page.locator("#selection-none").click();
  await page.locator("#selection-total").filter({ hasText: "0 / 2" }).waitFor();
  await page.locator('[data-select-path="new.zip"]').check();
  await page.locator("#selection-total").filter({ hasText: "1 / 2" }).waitFor();
  await page.locator("#selection-close").click();
  await page.locator("#refresh-paused").check();
  await page.locator("#refresh-apply").click();
  await page.locator("#refresh-dialog").waitFor({ state: "hidden" });
  const { files } = await (
    await page.request.get(base + `/api/jobs/${id}/files?limit=200`)
  ).json();
  assert.equal(files.length, 152);
  assert.ok(files.some((f) => f.name === "readme.txt"));
  assert.ok(files.some((f) => f.name === "new.zip"));
  assert.equal(files.find((f) => f.name === "roms/game-000.zip").size, 123456);
  await page.request.post(base + `/api/jobs/${id}/remove`, { data: {} });
  console.log(
    "Manifest refresh UI passed: new/changed/absent preview, selective application and retained local files.",
  );
} finally {
  await browser.close();
}
