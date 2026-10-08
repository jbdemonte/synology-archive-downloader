import assert from "node:assert/strict";
import { chromium } from "playwright";

const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1000, height: 720 },
});
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
const job = {
  id: "eta",
  identifier: "large-archive",
  title: "Large archive",
  status: "running",
  file_count: 42000,
  completed_files: 9000,
  active_files: 5,
  failed_files: 0,
  total_size: 144.4 * 1024 ** 3,
  downloaded: 30 * 1024 ** 3,
  speed: 1024 ** 2,
  destination: "/volume1/Archives",
  unknown_sizes: 0,
  average_speed: 123456,
  average_window_seconds: 15,
  eta_seconds: null,
  eta_state: "measuring",
  eta_lower_bound: false,
};
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
    else if (url.pathname === "/api/jobs") body = { jobs: [job] };
    else if (url.pathname.endsWith("/activity"))
      body = {
        active: [],
        queued: [],
        errors: [],
        counts: { completed: 9000, downloading: 5, queued: 32995, error: 0 },
      };
    else throw new Error(url.pathname);
    await route.fulfill({ json: body });
  });
  await page.goto(base);
  await page
    .locator(".remaining-time")
    .getByText("Restant : Calcul…", { exact: true })
    .waitFor();
  assert.match(
    await page.locator("#stat-bytes").innerText(),
    /30 Gio\s*\/ 144,4 Gio/,
  );
  job.eta_state = "ready";
  job.eta_seconds = 90000; // Must use the server estimate, not instantaneous speed.
  job.average_window_seconds = 300;
  await page
    .locator(".remaining-time")
    .getByText("Restant : ≈ 1 j 1 h", { exact: true })
    .waitFor();
  assert.match(
    await page.locator(".remaining-time").getAttribute("title"),
    /Moyenne sur 5 min/,
  );
  job.eta_lower_bound = true;
  job.unknown_sizes = 1;
  await page
    .locator(".remaining-time")
    .getByText("Restant : ≥ 1 j 1 h", { exact: true })
    .waitFor();
  assert.match(await page.locator("#stat-bytes").innerText(), /Gio \+/);
  assert.match(
    await page.locator(".remaining-time").getAttribute("title"),
    /taille inconnue/,
  );
  for (const width of [1360, 1000, 800]) {
    await page.setViewportSize({ width, height: 720 });
    const clipped = await page
      .locator("#stat-bytes, .remaining-time")
      .evaluateAll((elements) =>
        elements
          .filter((element) => element.scrollWidth > element.clientWidth)
          .map((element) => element.textContent),
      );
    assert.deepEqual(clipped, [], `Summary and ETA fit at ${width}px`);
  }
  job.eta_seconds = null;
  job.eta_state = "stalled";
  await page
    .locator(".remaining-time")
    .getByText("Restant : En attente de débit", { exact: true })
    .waitFor();
  job.status = "paused";
  await page.waitForFunction(() => !document.querySelector(".remaining-time"));
  job.status = "running";
  job.eta_state = "measuring";
  await page
    .locator(".remaining-time")
    .getByText("Restant : Calcul…", { exact: true })
    .waitFor();
  job.eta_state = "ready";
  job.eta_seconds = 1;
  job.eta_lower_bound = false;
  await page
    .locator(".remaining-time")
    .getByText("Restant : ≈ 1 min", { exact: true })
    .waitFor();
  assert.deepEqual(errors, []);
  console.log(
    "ETA passed: warmup, server average, unknown sizes, stalls, pause/resume, byte totals and compact layout.",
  );
} finally {
  await browser.close();
}
