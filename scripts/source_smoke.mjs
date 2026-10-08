import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({ locale: "fr-FR" });
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const jobs = [];
try {
  await page.context().route("https://archive.org/**", (route) =>
    route.fulfill({
      contentType: "text/html",
      body: "<title>Archive.org test fixture</title>",
    }),
  );
  for (const identifier of ["demo-one", "demo-two"]) {
    const { id } = await (
      await page.request.post(base + "/api/jobs", {
        data: { url: identifier, paused: true },
      })
    ).json();
    jobs.push({ id, identifier });
  }
  await page.goto(base);
  for (const job of jobs) {
    const link = page.locator(`[data-source-job="${job.id}"]`);
    await link.waitFor();
    assert.equal(
      await link.getAttribute("href"),
      `https://archive.org/details/${job.identifier}`,
    );
    assert.match(await link.getAttribute("rel"), /noopener/);
    const [popup] = await Promise.all([
      page.waitForEvent("popup"),
      link.click(),
    ]);
    await popup.waitForLoadState("domcontentloaded");
    assert.equal(popup.url(), `https://archive.org/details/${job.identifier}`);
    assert.ok(page.url().startsWith(base));
    await popup.close();
  }
  await page.locator(`[data-job="${jobs[1].id}"] strong`).click();
  const [popup] = await Promise.all([
    page.waitForEvent("popup"),
    page.locator("#detail-source").click(),
  ]);
  await popup.waitForLoadState("domcontentloaded");
  assert.equal(popup.url(), "https://archive.org/details/demo-two");
  await popup.close();
  assert.equal(page.context().pages().length, 1);
  console.log(
    "Source links passed: each archive and the selected task open the correct details page in a separate tab.",
  );
} finally {
  for (const job of jobs)
    await page.request.post(base + `/api/jobs/${job.id}/remove`, { data: {} });
  await browser.close();
}
