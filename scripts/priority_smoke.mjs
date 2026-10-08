import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({ locale: "fr-FR" });
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
  await page.locator(`.job-row[data-job="${ids[1]}"]`).click();
  await page.locator("#job-priority").selectOption("1");
  await page.waitForFunction(
    (id) => document.querySelector(".job-row").dataset.job === id,
    ids[1],
  );
  assert.equal(
    (await (await page.request.get(base + "/api/jobs")).json()).jobs[0]
      .priority,
    1,
  );
  await page.locator("#job-priority").selectOption("0");
  await page.waitForFunction(
    (id) => document.querySelector(".job-row").dataset.job === id,
    ids[0],
  );
  await page.locator("#queue-up").click();
  await page.waitForFunction(
    (id) => document.querySelector(".job-row").dataset.job === id,
    ids[1],
  );
  await page.locator(`[data-expand="${ids[1]}"][data-prefix=""]`).click();
  const star = page.locator(".file-priority").nth(1);
  const fileId = Number(await star.getAttribute("data-priority-file"));
  await star.click();
  await page
    .locator(
      `.file-priority[data-priority-file="${fileId}"][aria-pressed="true"]`,
    )
    .waitFor();
  const activity = await (
    await page.request.get(base + `/api/jobs/${ids[1]}/activity`)
  ).json();
  assert.equal(activity.queued[0].id, fileId);
  for (const id of ids)
    await page.request.post(base + `/api/jobs/${id}/remove`, { data: {} });
  console.log(
    "Priority UI passed: archive priority, queue order and file promotion match scheduler order.",
  );
} finally {
  await browser.close();
}
