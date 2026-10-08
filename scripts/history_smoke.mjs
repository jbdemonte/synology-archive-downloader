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
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
let holdSix = false,
  releaseSix,
  enteredSix;
const sixRequested = new Promise((resolve) => {
  enteredSix = resolve;
});
const sixGate = new Promise((resolve) => {
  releaseSix = resolve;
});
try {
  await page.route(/\/api\/jobs(?:\?|$)/, async (route) => {
    const window = Number(
      new URL(route.request().url()).searchParams.get("history_window") || 3600,
    );
    if (window === 21600 && holdSix) {
      enteredSix();
      await sixGate;
    }
    await route.fulfill({
      json: {
        jobs: [],
        history: {
          values: Array.from({ length: 120 }, (_, i) => (i % 2 ? 65536 : 0)),
          period_seconds: window / 120,
          window_seconds: window,
          end_time: Math.floor(Date.now() / 30000) * 30,
        },
      },
    });
  });
  await page.goto(base);
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
  // A slow previous range must never replace a more recently selected range.
  holdSix = true;
  await page.locator("#history-window").selectOption("21600");
  await sixRequested;
  assert.ok(await page.locator("#history-loading").isVisible());
  await page.locator("#history-window").selectOption("86400");
  releaseSix();
  await page
    .locator("#history-start")
    .filter({ hasText: /^-24\s*h$/ })
    .waitFor();
  assert.ok(await page.locator("#history-loading").isHidden());
  assert.match(await bins.last().locator("title").textContent(), /\d{2}:\d{2}/);
  holdSix = false;
  for (const hours of [6, 12, 24]) {
    await page.locator("#history-window").selectOption(String(hours * 3600));
    await page
      .locator("#history-start")
      .filter({ hasText: new RegExp(`^-${hours}\\s*h$`) })
      .waitFor();
    assert.equal(await page.locator("#history-chart rect title").count(), 120);
  }
  await page.reload();
  await page.locator("#history-panel summary").click();
  await page
    .locator("#history-start")
    .filter({ hasText: /^-24\s*h$/ })
    .waitFor();
  assert.equal(await page.locator("#history-window").inputValue(), "86400");
  // Check the real API too, independently of the illustrative browser route.
  for (const window of [3600, 21600, 43200, 86400]) {
    const response = await page.request.get(
      `${base}/api/jobs?history_window=${window}`,
    );
    assert.equal(response.status(), 200);
    const { history } = await response.json();
    assert.equal(history.window_seconds, window);
    assert.equal(history.period_seconds, window / 120);
    assert.equal(history.values.length, 120);
  }
  assert.equal(
    (
      await page.request.get(base + "/api/jobs?history_window=99999999")
    ).status(),
    400,
  );
  await page.setViewportSize({ width: 390, height: 844 });
  assert.ok(
    await page
      .locator("#history-chart")
      .evaluate((el) => el.getBoundingClientRect().right <= innerWidth),
  );
  assert.ok(
    await page
      .locator("#history-window")
      .evaluate((el) => el.getBoundingClientRect().right <= innerWidth),
  );
  console.log(
    "History passed: four periods, real API aggregation, stale-response protection, remembered choice, hover intervals and responsive layout.",
  );
} finally {
  releaseSix();
  await browser.close();
}
