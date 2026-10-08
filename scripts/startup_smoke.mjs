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
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
};
const auth = deferred(),
  settings = deferred(),
  jobs = deferred();
const settingsRequested = deferred(),
  jobsRequested = deferred();
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  const { id } = await (
    await page.request.post(base + "/api/jobs", {
      data: { url: "demo-one", paused: true },
    })
  ).json();
  await page.route("**/api/auth", async (route) => {
    await auth.promise;
    await route.continue();
  });
  await page.route("**/api/settings", async (route) => {
    settingsRequested.resolve();
    await settings.promise;
    await route.continue();
  });
  let fail = true;
  await page.route("**/api/jobs", async (route) => {
    jobsRequested.resolve();
    await jobs.promise;
    if (fail)
      await route.fulfill({
        status: 503,
        json: { error: "Service indisponible" },
      });
    else await route.continue();
  });
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page.locator("#startup-screen").waitFor({ state: "visible" });
  assert.equal(
    await page.locator("#startup-screen").getAttribute("aria-busy"),
    "true",
  );
  assert.ok(await page.locator("#application").isHidden());
  assert.ok(await page.locator("#empty").isHidden());
  auth.resolve();
  await settingsRequested.promise;
  assert.ok(await page.locator("#startup-screen").isVisible());
  assert.ok(await page.locator("#empty").isHidden());
  settings.resolve();
  await jobsRequested.promise;
  assert.ok(await page.locator("#application").isHidden());
  jobs.resolve();
  await page
    .locator("#startup-error")
    .filter({ hasText: "Service indisponible" })
    .waitFor();
  assert.ok(await page.locator("#application").isHidden());
  assert.ok(await page.locator("#login-screen").isHidden());
  fail = false;
  await page.locator("#startup-retry").click();
  await page.locator(".job-row").filter({ hasText: "demo-one" }).waitFor();
  assert.ok(await page.locator("#startup-screen").isHidden());
  assert.ok(await page.locator("#empty").isHidden());
  await page.request.post(base + `/api/jobs/${id}/remove`, { data: {} });
  await page.locator("#empty").waitFor({ state: "visible" });
  assert.ok(await page.locator("#startup-screen").isHidden());
  assert.deepEqual(errors, []);
  console.log(
    "Startup passed: delayed auth/settings/tasks never flash an empty list; service errors retry; a confirmed empty queue displays normally.",
  );
} finally {
  await browser.close();
}
