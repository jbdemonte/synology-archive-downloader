import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1000, height: 580 },
});
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
try {
  await page.goto(base);
  await page.locator("#settings-open").click();
  await page.locator(".settings-extra summary").click();
  await page.locator("#schedule-enabled").check();
  await page.locator("#schedule-start").fill("22:00");
  await page.locator("#schedule-end").fill("06:00");
  await page.locator("#schedule-outside").selectOption("limited");
  await page.locator("#schedule-limit").fill("500");
  await page.locator('#schedule-days input[value="2"]').uncheck();
  await page.locator('#settings-form [type="submit"]').click();
  await page.locator("#settings-dialog").waitFor({ state: "hidden" });
  const settings = await (
    await page.request.get(base + "/api/settings")
  ).json();
  assert.equal(settings.schedule_enabled, true);
  assert.equal(settings.schedule_start, 1320);
  assert.equal(settings.schedule_end, 360);
  assert.equal(settings.schedule_limit_kib, 500);
  assert.deepEqual(settings.schedule_days, [0, 1, 3, 4, 5, 6]);
  await page.locator("#settings-open").click();
  await page.locator("#settings-dialog").waitFor({ state: "visible" });
  assert.equal(await page.locator("#schedule-start").inputValue(), "22:00");
  await page.request.post(base + "/api/settings", {
    data: { schedule_enabled: false },
  });
  console.log(
    "Schedule UI passed: weekdays, overnight slot, alternate rate and persistence.",
  );
} finally {
  await browser.close();
}
