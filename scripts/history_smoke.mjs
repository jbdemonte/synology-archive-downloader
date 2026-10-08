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
          values: Array.from({ length: 120 }, (_, i) => (i % 2 ? 65536 : 0)),
          period_seconds: 30,
          window_seconds: 3600,
        },
      },
    }),
  );
  await page.goto(process.env.TEST_URL || "http://127.0.0.1:8275");
  await page.locator("#history-panel summary").click();
  await page.locator("#history-peak").filter({ hasText: "64 Kio/s" }).waitFor();
  assert.equal(await page.locator("#history-chart rect title").count(), 120);
  assert.match(
    await page.locator("#history-start").textContent(),
    /^-60\s*min$/,
  );
  assert.match(
    await page.locator("#history-middle").textContent(),
    /^-30\s*min$/,
  );
  assert.match(await page.locator("#history-end").textContent(), /^0\s*min$/);
  const bins = page.locator("#history-chart rect");
  assert.match(
    await bins.first().locator("title").textContent(),
    /-60\s*min → -59,5\s*min/,
  );
  assert.match(
    await bins.last().locator("title").textContent(),
    /-0,5\s*min → 0\s*min/,
  );
  assert.equal(await bins.last().getAttribute("x"), "595");
  assert.equal(await bins.last().getAttribute("width"), "5");
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
    "Hour graph passed: 120 averages, time axis, hover intervals and responsive width.",
  );
} finally {
  await browser.close();
}
