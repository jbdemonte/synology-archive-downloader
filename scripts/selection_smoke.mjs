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
const errors = [];
page.on("pageerror", (e) => errors.push(e.message));
try {
  await page.goto(base);
  await page.locator("#add-open").click();
  await page.locator("#urls").fill("demo-select");
  await page.locator("#start-paused").check();
  await page.locator("#inspect-button").click();
  await page.locator("[data-select-plan]").click();
  await page.locator("#selection-none").click();
  await page
    .locator("#selection-total")
    .filter({ hasText: "0 / 151" })
    .waitFor();
  await page.locator("#selection-pattern").fill("*.zip");
  await page.locator("#selection-include").click();
  await page
    .locator("#selection-total")
    .filter({ hasText: "150 / 151" })
    .waitFor();
  await page.locator("[data-selection-folder]").click();
  await page.locator("#selection-rows input").first().uncheck();
  await page
    .locator("#selection-total")
    .filter({ hasText: "149 / 151" })
    .waitFor();
  await page.locator("#selection-next").click();
  await page
    .locator("#selection-rows")
    .getByText("game-100.zip", { exact: true })
    .waitFor();
  assert.equal(await page.locator("#selection-rows input").count(), 50);
  assert.ok(
    await page
      .locator("#selection-close")
      .evaluate((el) => el.getBoundingClientRect().bottom <= innerHeight),
  );
  await page.locator("#selection-close").click();
  await page.locator("#create-button").click();
  await page.locator(".job-row").filter({ hasText: "demo-select" }).waitFor();
  const { jobs } = await (await page.request.get(base + "/api/jobs")).json();
  const job = jobs.find((j) => j.identifier === "demo-select");
  assert.equal(job.file_count, 149);
  const { files } = await (
    await page.request.get(base + `/api/jobs/${job.id}/files?limit=200`)
  ).json();
  assert.ok(
    files.every(
      (f) => f.name.endsWith(".zip") && !f.name.endsWith("game-000.zip"),
    ),
  );
  await page.request.post(base + `/api/jobs/${job.id}/remove`, { data: {} });
  assert.deepEqual(errors, []);
  console.log(
    "File selection passed: patterns, folder navigation, pagination, counts, small window, exact job contents.",
  );
} finally {
  await browser.close();
}
