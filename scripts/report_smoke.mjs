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
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  const { id } = await (
    await page.request.post(base + "/api/jobs", {
      data: { url: "demo-one", paused: true },
    })
  ).json();
  await page.request.post(base + "/api/settings", { data: { language: "fr" } });
  await page.route("**/api/jobs", async (route) => {
    const result = await (await route.fetch()).json();
    result.jobs.find((j) => j.id === id).incident_count = 2;
    await route.fulfill({ json: result });
  });
  let release;
  let wait = new Promise((resolve) => {
    release = resolve;
  });
  let fail = false;
  const text =
    "RAPPORT DE TÉLÉCHARGEMENT\nHISTORIQUE DES ERREURS\nRésolu : archive.zip\n<img src=x onerror=alert(1)>\nConnection reset";
  await page.route(`**/api/jobs/${id}/report?*`, async (route) => {
    await wait;
    if (fail)
      return route.fulfill({
        status: 503,
        json: { error: "Service indisponible" },
      });
    const offset = Number(
      new URL(route.request().url()).searchParams.get("offset"),
    );
    await route.fulfill({
      json: {
        filename: `ArchiveStation-report-${id}.txt`,
        content: offset ? "Deuxième page" : text,
        offset,
        total: 220,
      },
    });
  });
  await page.goto(base);
  const icon = page.locator(`[data-report-job="${id}"]`);
  await icon.waitFor();
  assert.match(await icon.getAttribute("aria-label"), /2 incidents consignés/);
  assert.ok((await icon.getAttribute("class")).includes("has-incidents"));
  await icon.click();
  await page.locator("#report-loading").waitFor({ state: "visible" });
  assert.ok(await page.locator("#report-refresh").isDisabled());
  // Reading starts at the title, without highlighting the close button.
  assert.equal(
    await page.evaluate(() => document.activeElement.id),
    "report-title",
  );
  assert.equal(
    await page
      .locator("#report-title")
      .evaluate((el) => getComputedStyle(el).outlineStyle),
    "none",
  );
  const close = page.locator("#report-dialog .close");
  assert.ok(
    await close.evaluate((el) => {
      const button = el.getBoundingClientRect();
      const icon = el.querySelector("svg").getBoundingClientRect();
      return (
        button.width === button.height &&
        Math.abs(icon.x + icon.width / 2 - button.x - button.width / 2) < 0.5 &&
        Math.abs(icon.y + icon.height / 2 - button.y - button.height / 2) < 0.5
      );
    }),
    "The close icon must be centered inside a square hit target",
  );
  await close.hover();
  await page.waitForFunction(
    () =>
      getComputedStyle(document.querySelector("#report-dialog .close"))
        .backgroundColor === "rgb(234, 242, 251)",
  );
  assert.ok(
    await close.evaluate((el) => {
      const style = getComputedStyle(el);
      return (
        style.borderRadius === "50%" &&
        style.backgroundColor !== "rgba(0, 0, 0, 0)"
      );
    }),
    "Hover must draw a circular background around the centered close icon",
  );
  // Tab still exposes a visible focus marker and reaches the close control.
  await page.keyboard.press("Tab");
  assert.ok(
    await close.evaluate(
      (el) =>
        document.activeElement === el &&
        el.matches(":focus-visible") &&
        getComputedStyle(el).outlineStyle === "solid",
    ),
  );
  release();
  await page.getByText("Connection reset", { exact: false }).waitFor();
  assert.equal(await page.locator("#report-text img").count(), 0);
  assert.equal(await page.locator("#report-text").textContent(), text);
  await page.locator("#report-next").click();
  await page.getByText("Deuxième page", { exact: true }).waitFor();
  assert.ok(await page.locator("#report-next").isDisabled());
  await page.locator("#report-refresh").click();
  await page.waitForFunction(
    () => !document.querySelector("#report-refresh").disabled,
  );
  assert.equal(
    await page.locator("#report-text").textContent(),
    "Deuxième page",
  );
  assert.equal(await page.locator("#report-page").textContent(), "2 / 2");
  await page.locator("#report-prev").click();
  await page.getByText("Connection reset", { exact: false }).waitFor();
  fail = true;
  await page.locator("#report-refresh").click();
  await page
    .locator("#report-error")
    .filter({ hasText: "Service indisponible" })
    .waitFor();
  fail = false;
  await page.locator("#report-refresh").click();
  await page.waitForFunction(
    () => !document.querySelector("#report-error").textContent,
  );
  await page.setViewportSize({ width: 800, height: 460 });
  assert.ok(
    await page
      .locator("#report-dialog")
      .evaluate((el) => el.getBoundingClientRect().bottom <= innerHeight),
  );
  assert.ok(
    await page
      .locator("#report-text")
      .evaluate((el) => el.getBoundingClientRect().right <= innerWidth),
  );
  await page.locator("#report-dialog [data-close]").first().click();
  // Keyboard opening and dismissal must remain available too.
  await icon.focus();
  await page.keyboard.press("Enter");
  await page.locator("#report-dialog").waitFor();
  assert.equal(
    await page.evaluate(() => document.activeElement.id),
    "report-title",
  );
  await page.keyboard.press("Tab");
  await page.keyboard.press("Enter");
  await page.locator("#report-dialog").waitFor({ state: "hidden" });
  // Clicking expandable sections must not leave the browser's native outline.
  const summary = page.locator("#history-panel summary");
  await summary.click();
  assert.equal(
    await summary.evaluate((el) => getComputedStyle(el).outlineStyle),
    "none",
  );
  await page.keyboard.press("Tab");
  await page.keyboard.press("Shift+Tab");
  assert.ok(
    await summary.evaluate(
      (el) =>
        document.activeElement === el &&
        getComputedStyle(el).outlineStyle === "solid",
    ),
  );
  await page.setViewportSize({ width: 1100, height: 650 });
  await page.locator(`[data-menu-job="${id}"]`).click();
  await page.locator("#read-report").click();
  await page
    .locator("#report-text")
    .filter({ hasText: "Connection reset" })
    .waitFor();
  assert.equal(page.context().pages().length, 1);
  await page.locator("#report-dialog [data-close]").first().click();
  assert.deepEqual(errors, []);
  await page.request.post(base + `/api/jobs/${id}/remove`, { data: {} });
  console.log(
    "Report reader passed: incident icon, loading, plain text, pagination, retry, compact window, centered close icon and mouse/keyboard focus.",
  );
} finally {
  await browser.close();
}
