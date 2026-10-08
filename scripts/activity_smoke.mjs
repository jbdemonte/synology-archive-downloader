import assert from "node:assert/strict";
import { chromium } from "playwright";
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1360, height: 840 },
});
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
let phase = 0,
  holdNext = false,
  releaseHeld,
  notifyHeld;
const held = new Promise((resolve) => {
  notifyHeld = resolve;
});
const file = (name, status) => ({
  name,
  status,
  size: 1048576,
  downloaded:
    status === "completed" ? 1048576 : status === "downloading" ? 512000 : 0,
  speed: status === "downloading" ? 102400 : 0,
});
try {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body;
    if (url.pathname === "/api/auth")
      body = { authenticated: true, mode: "local" };
    else if (url.pathname === "/api/settings")
      body = {
        language: "fr",
        download_dir: "/volume1/Archives",
        connections: 5,
        version: "test",
      };
    else if (url.pathname === "/api/jobs")
      body = {
        jobs: [
          {
            id: "large",
            identifier: "large-archive",
            title: "Large archive",
            status: "running",
            file_count: 42000,
            completed_files: 40000 + phase,
            active_files: 2,
            failed_files: 1,
            total_size: 42000 * 1048576,
            downloaded: 40000 * 1048576,
            speed: 204800,
            destination: "/volume1/Archives",
          },
        ],
      };
    else if (url.pathname.endsWith("/activity")) {
      body = {
        active: [
          file(
            phase ? "roms/next-00.zip" : "deeply/nested/live.zip",
            "downloading",
          ),
          file("another/folder/live.zip", "downloading"),
        ],
        queued: Array.from({ length: 10 }, (_, i) =>
          file(`roms/next-${String(i + phase).padStart(2, "0")}.zip`, "queued"),
        ),
        errors: [
          { ...file("docs/denied.txt", "error"), error: "Access denied" },
        ],
        counts: {
          downloading: 2,
          queued: 1997 - phase,
          completed: 40000 + phase,
          error: 1,
        },
      };
      if (holdNext) {
        holdNext = false;
        notifyHeld();
        await new Promise((resolve) => {
          releaseHeld = resolve;
        });
      }
    } else if (url.pathname.endsWith("/files")) {
      const status = url.searchParams.get("status");
      body =
        status === "error"
          ? { files: [file("docs/denied.txt", "error")], total: 1, offset: 0 }
          : {
              files: [
                file("finished/old.zip", "completed"),
                ...(phase ? [file("deeply/nested/live.zip", "completed")] : []),
              ],
              total: 40000 + phase,
              offset: Number(url.searchParams.get("offset")),
            };
    } else if (url.pathname.endsWith("/tree"))
      body = {
        children: [
          {
            kind: "folder",
            name: "deeply",
            path: "deeply",
            size: 1048576,
            downloaded: 512000,
            status: "downloading",
            file_count: 1,
            completed_files: 0,
          },
        ],
        total: 1,
      };
    else throw new Error(url.pathname);
    await route.fulfill({ json: body });
  });
  await page.goto(base);
  await page
    .locator('.child-row[data-file-path="deeply/nested/live.zip"]')
    .waitFor();
  assert.equal(await page.locator(".live-file").count(), 2);
  assert.equal(await page.locator(".child-row").count(), 13);
  assert.equal(
    await page.locator(".page-row").count(),
    0,
    "Activity has no pagination",
  );
  assert.equal(
    await page.getByText("deeply/nested", { exact: true }).isVisible(),
    true,
  );
  assert.equal(
    await page.getByText("another/folder", { exact: true }).isVisible(),
    true,
  );
  assert.equal(
    await page.locator(".child-row").first().getAttribute("data-file-path"),
    "deeply/nested/live.zip",
  );
  await page.locator('[data-file-view="completed"]').click();
  await page.getByText("old.zip", { exact: true }).waitFor();
  assert.equal(await page.locator(".live-file").count(), 0);
  assert.equal(await page.locator(".page-row").count(), 1);
  await page.locator('[data-file-view="error"]').click();
  await page.getByText("denied.txt", { exact: true }).waitFor();
  assert.equal(await page.getByText("old.zip", { exact: true }).count(), 0);
  await page.locator('[data-file-view="tree"]').click();
  await page
    .getByRole("button", { name: "Déplier deeply", exact: true })
    .waitFor();
  await page.locator('[data-file-view="activity"]').click();
  phase = 1;
  await page.locator('.live-file[data-file-path="roms/next-00.zip"]').waitFor();
  assert.equal(
    await page
      .locator('.child-row[data-file-path="deeply/nested/live.zip"]')
      .count(),
    0,
    "Finished files leave the live view",
  );
  await page.locator('[data-file-view="completed"]').click();
  await page
    .locator('.child-row[data-file-path="deeply/nested/live.zip"]')
    .waitFor();
  holdNext = true;
  await page.locator('[data-file-view="activity"]').click();
  await held;
  await page.locator('[data-file-view="completed"]').click();
  await page.getByText("old.zip", { exact: true }).waitFor();
  releaseHeld();
  await page.waitForTimeout(1700);
  assert.equal(
    await page
      .locator('[data-file-view="completed"]')
      .getAttribute("aria-pressed"),
    "true",
  );
  assert.equal(
    await page.locator(".live-file").count(),
    0,
    "Late activity responses cannot replace completed files",
  );
  assert.equal(
    await page
      .locator('[data-file-view="completed"]')
      .evaluate((el) => document.activeElement === el),
    true,
    "Live refresh preserves keyboard focus",
  );
  await page.locator('[data-file-view="activity"]').click();
  await page.locator(".live-file").first().waitFor();
  await page.screenshot({ path: "/tmp/archive-station-activity.png" });
  await page.setViewportSize({ width: 800, height: 580 });
  assert.equal(
    await page
      .locator(".nav-label")
      .first()
      .evaluate((el) => el.getBoundingClientRect().height < 20),
    true,
    "Short navigation label fits on one line",
  );
  assert.deepEqual(errors, []);
  console.log(
    "Large archive UI passed: automatic activity, live transfers, next files, completed/error views, tree, transitions and stale response protection.",
  );
} finally {
  await browser.close();
}
