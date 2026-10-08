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
try {
  const ids = [];
  for (const url of ["demo-one", "demo-two"])
    ids.push(
      (
        await (
          await page.request.post(base + "/api/jobs", {
            data: { url, paused: true },
          })
        ).json()
      ).id,
    );
  await page.goto(base);
  await page.locator(".job-row").first().waitFor();
  await page.locator("#select-visible").check();
  assert.equal(await page.locator("[data-select-job]:checked").count(), 2);
  await page.locator("#resume").click();
  await page.waitForFunction(() =>
    [...document.querySelectorAll(".job-row .badge")].every(
      (el) => el.textContent === "En attente",
    ),
  );
  await page.locator("#search").fill("demo-one");
  assert.equal(await page.locator("[data-select-job]:checked").count(), 1);
  await page.locator("#pause").click();
  await page.locator(".job-row .badge.paused").waitFor();
  const jobs = (await (await page.request.get(base + "/api/jobs")).json()).jobs;
  assert.equal(jobs.find((j) => j.id === ids[1]).status, "queued");
  await page.locator("#global-action").selectOption("pause_all");
  await page.locator("#search").fill("");
  await page.waitForFunction(
    () => document.querySelectorAll(".job-row .badge.paused").length === 2,
  );
  await page.locator("#select-visible").check();
  await page.locator("#remove").click();
  await page.locator("#remove-confirm").click();
  await page.locator("#empty").waitFor({ state: "visible" });
  assert.equal(
    (await (await page.request.get(base + "/api/jobs")).json()).jobs.length,
    0,
  );
  console.log(
    "Bulk UI passed: multiple selection, resume, filtering safety, global pause and removal.",
  );
} finally {
  await browser.close();
}
