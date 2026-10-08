import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1100, height: 650 },
});
try {
  await page.route("**/api/jobs", (route) =>
    route.fulfill({
      json: {
        jobs: [],
        history: {
          values: Array.from({ length: 60 }, (_, i) => (i % 2 ? 65536 : 0)),
          period_seconds: 5,
        },
      },
    }),
  );
  await page.goto(process.env.TEST_URL || "http://127.0.0.1:8275");
  await page.locator("#history-panel summary").click();
  await page.locator("#history-peak").filter({ hasText: "64 Kio/s" }).waitFor();
  assert.equal(await page.locator("#history-chart rect title").count(), 60);
  assert.ok(
    (
      await page.locator("#history-chart polyline").getAttribute("points")
    ).includes("88"),
  );
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .locator("#history-chart")
      .evaluate((el) => el.getBoundingClientRect().right <= innerWidth),
  );
  console.log(
    "History graph passed: rates, samples, accessible hover values and responsive width.",
  );
} finally {
  await browser.close();
}
