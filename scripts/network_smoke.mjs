import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({ locale: "en-US" });
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.request.post(base + "/api/settings", { data: { language: "en" } });
  await page.goto(base);
  await page.locator("#application").waitFor({ state: "visible" });
  // A mocked fetch body that stalls must release the polling lock on timeout.
  await page.clock.install();
  await page.evaluate(() => {
    window.realFetch = window.fetch;
    window.fetch = (url, options) =>
      String(url).includes("/api/jobs")
        ? new Promise((resolve, reject) =>
            options.signal.addEventListener("abort", () =>
              reject(new DOMException("aborted", "AbortError")),
            ),
          )
        : window.realFetch(url, options);
  });
  await page.clock.runFor(1600);
  await page.clock.runFor(20001);
  await page.locator("#connection").waitFor({ state: "visible" });
  assert.match(await page.locator("#connection").textContent(), /service/i);
  await page.evaluate(() => {
    window.fetch = window.realFetch;
  });
  await page.clock.runFor(1600);
  await page.locator("#connection").waitFor({ state: "hidden" });
  // Hidden tabs suspend both recurring jobs and settings requests.
  let calls = 0;
  await page.route("**/api/**", async (route) => {
    calls++;
    await route.continue();
  });
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => true,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.clock.runFor(60000);
  assert.equal(calls, 0);
  await page.evaluate(() => {
    Object.defineProperty(document, "hidden", {
      configurable: true,
      get: () => false,
    });
    document.dispatchEvent(new Event("visibilitychange"));
  });
  await page.waitForFunction(
    () => !document.querySelector("#application").hidden,
  );
  await page.waitForTimeout(100);
  assert.ok(calls >= 2);
  await page.unroute("**/api/**");
  // A temporary DSM 401 can be retried in the same window.
  let expired = true;
  await page.route("**/api/auth", (route) =>
    route.fulfill({ json: { mode: "dsm", authenticated: true } }),
  );
  await page.route("**/api/jobs", (route) =>
    expired
      ? route.fulfill({ status: 401, json: { error: "Session expired" } })
      : route.continue(),
  );
  await page.reload();
  await page.locator("#dsm-retry").waitFor({ state: "visible" });
  expired = false;
  await page.locator("#dsm-retry").click();
  await page.locator("#application").waitFor({ state: "visible" });
  assert.deepEqual(errors, []);
  console.log(
    "Network UI passed: timed-out polling recovers, hidden tabs stop polling, and DSM session recovery retries in place.",
  );
} finally {
  await page.request.post(base + "/api/settings", {
    data: { language: "auto" },
  });
  await browser.close();
}
