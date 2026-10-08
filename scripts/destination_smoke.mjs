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
const job = {
  id: "one",
  identifier: "one-archive",
  title: "One archive",
  status: "running",
  file_count: 10,
  completed_files: 1,
  total_size: 10000,
  downloaded: 1000,
  speed: 100,
  active_files: 1,
  failed_files: 0,
  destination: "/volume1/Archives",
};
const settings = {
  language: "fr",
  download_dir: "/volume1/Archives",
  connections: 5,
  speed_limit_kib: 0,
  retries: 4,
  verify_checksums: true,
  version: "test",
};
const saved = [];
try {
  await page.route("**/api/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let body;
    if (path === "/api/auth") body = { authenticated: true, mode: "local" };
    else if (path === "/api/settings") {
      if (route.request().method() === "POST") {
        const data = route.request().postDataJSON();
        saved.push(data);
        Object.assign(settings, data);
      }
      body = {
        ...settings,
        destination_locked:
          ["running", "queued"].includes(job.status) || job.active_files > 0,
      };
    } else if (path === "/api/jobs") body = { jobs: [job] };
    else if (path.endsWith("/activity"))
      body = {
        active: [],
        queued: [],
        errors: [],
        counts: {
          downloading: job.active_files,
          queued: 8,
          completed: 1,
          error: 0,
        },
      };
    else if (path.endsWith("/files")) body = { files: [], total: 0, offset: 0 };
    else if (path === "/api/folders")
      body = {
        path: "/volume1/Archives",
        parent: "",
        writable: true,
        folders: [],
      };
    else throw new Error(path);
    await route.fulfill({ json: body });
  });
  await page.goto(process.env.TEST_URL || "http://127.0.0.1:8275");
  await page.locator(".job-row").waitFor();
  await page.locator("#settings-open").click();
  await page.locator("#settings-dialog").waitFor();
  assert.equal(await page.locator("#setting-destination").isDisabled(), true);
  assert.equal(await page.locator("#browse-settings").isDisabled(), true);
  assert.equal(await page.locator("#destination-edit").isDisabled(), true);
  assert.equal(
    await page.locator("#destination-lock-notice").isVisible(),
    true,
  );
  await page.locator("#setting-connections").fill("7");
  await page.locator('#settings-form button[type="submit"]').click();
  await page.waitForFunction(
    () => !document.querySelector("#settings-dialog").open,
  );
  assert.equal(saved[0].connections, 7);
  assert.equal(Object.hasOwn(saved[0], "download_dir"), false);
  await page.locator("#settings-open").click();
  await page.locator("#settings-dialog").waitFor();
  job.status = "paused"; // A socket may still be finishing its current read.
  await page
    .locator(".job-row")
    .getByText("En pause", { exact: true })
    .waitFor();
  assert.equal(await page.locator("#setting-destination").isDisabled(), true);
  job.active_files = 0;
  await page.waitForFunction(
    () => !document.querySelector("#setting-destination").disabled,
  );
  assert.equal(
    await page.locator("#destination-lock-notice").isVisible(),
    false,
  );
  await page.locator("#setting-destination").fill("/volume1/Draft");
  await page.locator("#browse-settings").click();
  await page.locator("#folder-dialog").waitFor();
  job.status = "running";
  job.active_files = 1;
  await page.waitForFunction(
    () => !document.querySelector("#folder-dialog").open,
  );
  assert.equal(await page.locator("#setting-destination").isDisabled(), true);
  assert.equal(
    await page.locator("#setting-destination").inputValue(),
    "/volume1/Archives",
  );
  assert.equal(
    await page.locator("#destination-lock-notice").isVisible(),
    true,
  );
  job.status = "completed";
  job.active_files = 0;
  await page.waitForFunction(
    () => !document.querySelector("#setting-destination").disabled,
  );
  await page.locator("#setting-destination").fill("/volume1/Next");
  await page.locator('#settings-form button[type="submit"]').click();
  await page.waitForFunction(
    () => !document.querySelector("#settings-dialog").open,
  );
  assert.equal(saved.at(-1).download_dir, "/volume1/Next");
  assert.equal(job.destination, "/volume1/Archives");
  assert.deepEqual(errors, []);
  console.log(
    "Destination UI passed: active/queued lock, stopping workers, live limits, open picker race, completion unlock and retained job paths.",
  );
} finally {
  await browser.close();
}
