import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({ locale: "fr-FR" });
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
try {
  await page.request.post(base + "/api/settings", {
    data: { disk_reserve_mib: 1000000000 },
  });
  await page.goto(base);
  await page.locator("#add-open").click();
  await page.locator("#urls").fill("demo-one");
  await page.locator("#inspect-button").click();
  await page.locator("#capacity-summary.error").waitFor();
  await page.locator("#create-button").click();
  await page
    .locator("#add-error")
    .filter({ hasText: "Espace disque insuffisant." })
    .waitFor();
  assert.equal(
    (await (await page.request.get(base + "/api/jobs")).json()).jobs.length,
    0,
  );
  await page.locator("#start-paused").check();
  await page.locator("#create-button").click();
  await page.locator("#add-dialog").waitFor({ state: "hidden" });
  const { jobs } = await (await page.request.get(base + "/api/jobs")).json();
  assert.equal(jobs[0].status, "paused");
  await page.request.post(base + `/api/jobs/${jobs[0].id}/remove`, {
    data: {},
  });
  await page.request.post(base + "/api/settings", {
    data: { disk_reserve_mib: 1024 },
  });
  console.log(
    "Capacity UI passed: reserve estimate, insufficient space refusal and add-paused recovery.",
  );
} finally {
  await browser.close();
}
