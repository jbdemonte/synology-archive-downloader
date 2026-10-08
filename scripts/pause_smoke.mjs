import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1100, height: 780 },
});
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const jobs = ["one", "two"].map((id) => ({
  id,
  identifier: id + "-archive",
  title: id + " archive",
  status: "running",
  file_count: 10,
  completed_files: 1,
  downloaded: 1000,
  total_size: 10000,
  speed: 100,
  failed_files: 0,
  active_files: 1,
  destination: "/volume1/Archives",
}));
const actions = [];
try {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body;
    if (path === "/api/auth") body = { authenticated: true, mode: "local" };
    else if (path === "/api/settings")
      body = {
        language: "fr",
        connections: 3,
        download_dir: "/volume1/Archives",
        version: "test",
      };
    else if (path === "/api/jobs") body = { jobs };
    else if (path.endsWith("/activity"))
      body = {
        active: [],
        queued: [],
        errors: [],
        counts: { downloading: 1, completed: 1, queued: 8, error: 0 },
      };
    else if (path.endsWith("/pause") || path.endsWith("/resume")) {
      const [, , , id, action] = path.split("/");
      actions.push([id, action]);
      jobs.find((job) => job.id === id).status =
        action === "pause" ? "paused" : "queued";
      body = { ok: true };
    } else throw new Error(path);
    await route.fulfill({ json: body });
  });
  const base = process.env.TEST_URL || "http://127.0.0.1:8275";
  await page.goto(base);
  await page.locator(".job-row").first().waitFor();
  assert.equal(await page.locator("#pause").isDisabled(), true);
  assert.equal(await page.locator("#selection-hint").isVisible(), true);
  assert.match(
    await page.locator("#selection-hint").innerText(),
    /Sélectionner une archive/,
  );
  await page.locator('.job-row[data-job="two"]').focus();
  await page.keyboard.press("Enter");
  assert.equal(await page.locator("#pause").isEnabled(), true);
  assert.equal(await page.locator("#selection-hint").isVisible(), false);
  await page.locator("#pause").click();
  await page
    .locator('.job-row[data-job="two"]')
    .getByText("En pause", { exact: true })
    .waitFor();
  assert.equal(jobs[0].status, "running", "Only the selected task is paused");
  await page.locator("#resume").click();
  await page
    .locator('.job-row[data-job="two"]')
    .getByText("En attente", { exact: true })
    .waitFor();
  await page.locator("#search").fill("one-archive");
  assert.equal(
    await page
      .locator('.job-row[data-job="one"]')
      .getAttribute("aria-selected"),
    "true",
  );
  assert.equal(await page.locator("#pause").isEnabled(), true);
  await page.locator("#search").fill("no-match");
  assert.equal(
    await page.locator("#pause").isDisabled(),
    true,
    "Never act on a hidden selection",
  );
  await page.locator("#search").fill("");
  assert.equal(await page.locator("#selection-hint").isVisible(), true);
  jobs.pop();
  await page.reload();
  await page.locator(".job-row").waitFor();
  assert.equal(
    await page.locator("#pause").isEnabled(),
    true,
    "The only archive is automatically selected on opening",
  );
  assert.equal(await page.locator("#selection-hint").isVisible(), false);
  await page.locator("#pause").click();
  await page
    .locator(".job-row")
    .getByText("En pause", { exact: true })
    .waitFor();
  assert.deepEqual(actions, [
    ["two", "pause"],
    ["two", "resume"],
    ["one", "pause"],
  ]);
  assert.deepEqual(errors, []);
  console.log(
    "Selection UI passed: visible guidance, mouse/keyboard controls, single-archive selection, pause/resume and hidden-selection safety.",
  );
} finally {
  await browser.close();
}
