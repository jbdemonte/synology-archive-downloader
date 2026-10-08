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
      data: { url: "demo-one", paused: true },
    })
  ).json();
  await page.goto(base);
  await page.locator("#repair").click();
  await page.locator("#repair-confirm").click();
  await page.locator("#repair-dialog").waitFor({ state: "hidden" });
  const { files } = await (
    await page.request.get(base + `/api/jobs/${id}/files`)
  ).json();
  assert.ok(files.every((f) => f.repair === 1 && f.status === "queued"));
  await page.waitForFunction(() => document.querySelector("#repair").disabled);
  await page.request.post(base + `/api/jobs/${id}/pause`, { data: {} });
  await page.request.post(base + `/api/jobs/${id}/remove`, { data: {} });
  console.log(
    "Repair UI passed: confirmation, queued background verification and active-task guard.",
  );
} finally {
  await browser.close();
}
