// Actual application screenshots using illustrative data only. Never connects
// to a NAS or Internet Archive and never reads a user's download list.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { mkdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright";

const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const out = new URL("../docs/images/", import.meta.url);
await mkdir(out, { recursive: true });
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "en-GB",
  viewport: { width: 1360, height: 840 },
  deviceScaleFactor: 1,
});
const demoTime = new Date("2026-10-08T14:20:00Z");
await page.clock.setFixedTime(demoTime);
const MiB = 1024 ** 2,
  GiB = 1024 ** 3;
const jobs = [
  {
    id: "demo-space",
    identifier: "space-photography",
    title: "Space Photography Collection",
    status: "running",
    file_count: 176,
    completed_files: 173,
    total_size: 6.8 * GiB,
    downloaded: 4.7 * GiB,
    speed: 3.4 * MiB,
    average_speed: 3.1 * MiB,
    average_window_seconds: 300,
    eta_seconds: 694,
    eta_state: "ready",
    eta_lower_bound: false,
    incident_count: 3,
    unresolved_incidents: 1,
  },
  {
    id: "demo-maps",
    identifier: "historical-maps",
    title: "Historical Maps and Atlases",
    status: "paused",
    file_count: 82,
    completed_files: 24,
    total_size: 2.3 * GiB,
    downloaded: 0.7 * GiB,
    speed: 0,
  },
  {
    id: "demo-books",
    identifier: "public-domain-books",
    title: "Public Domain Book Collection",
    status: "completed",
    file_count: 36,
    completed_files: 36,
    total_size: 850 * MiB,
    downloaded: 850 * MiB,
    speed: 0,
  },
].map((job) => ({
  ...job,
  destination: "/volume1/Download/Archives",
  unknown_sizes: 0,
  failed_files: 0,
}));
// Render the demo text with the same Python formatter used by the NAS.
const reportJob = {
  ...jobs[0],
  created: demoTime.getTime() / 1000 - 3600,
  finished_at: null,
  error_history_since: demoTime.getTime() / 1000 - 3600,
  source_url: "https://archive.org/details/space-photography",
  mode: "all",
  pattern: "",
  downloaded: Math.round(jobs[0].downloaded),
  total_size: Math.round(jobs[0].total_size),
  completed_bytes: Math.round(4 * GiB),
};
const reportText = execFileSync(
  process.env.PYTHON ||
    fileURLToPath(new URL("../.venv/bin/python", import.meta.url)),
  [
    "-c",
    `import json, sys
from archive_station.reports import render_report
data = json.load(sys.stdin)
print(render_report(data["job"], {"language": "en", "verify_checksums": True}, data["now"], data["incidents"]), end="")`,
  ],
  {
    encoding: "utf8",
    env: {
      ...process.env,
      PYTHONPATH: fileURLToPath(new URL("../src", import.meta.url)),
    },
    input: JSON.stringify({
      job: reportJob,
      now: demoTime.getTime() / 1000,
      incidents: [
        {
          name: "photographs/earth-from-orbit.tif",
          first_at: reportJob.created + 240,
          last_at: reportJob.created + 300,
          occurrences: 2,
          attempt: 2,
          message: "Connection reset by peer",
          resolved_at: reportJob.created + 420,
        },
        {
          name: "photographs/solar-eclipse.tif",
          first_at: reportJob.created + 3500,
          last_at: reportJob.created + 3500,
          occurrences: 1,
          attempt: 1,
          message: "HTTP Error 503: Service Unavailable — retry scheduled",
          resolved_at: null,
        },
      ],
    }),
  },
);
const tree = {
  "": [
    {
      kind: "folder",
      name: "photographs",
      path: "photographs",
      size: 6.2 * GiB,
      downloaded: 4.3 * GiB,
      speed: 3.4 * MiB,
      status: "downloading",
      file_count: 174,
      completed_files: 171,
    },
    {
      kind: "file",
      name: "collection-index.xml",
      path: "collection-index.xml",
      size: 870 * 1024,
      downloaded: 870 * 1024,
      speed: 0,
      status: "completed",
    },
    {
      kind: "file",
      name: "readme.txt",
      path: "readme.txt",
      size: 2400,
      downloaded: 2400,
      speed: 0,
      status: "completed",
    },
  ],
  photographs: [
    {
      kind: "file",
      name: "earth-from-orbit.tif",
      path: "photographs/earth-from-orbit.tif",
      size: 480 * MiB,
      downloaded: 480 * MiB,
      speed: 0,
      status: "completed",
    },
    {
      kind: "file",
      name: "lunar-surface.tif",
      path: "photographs/lunar-surface.tif",
      size: 640 * MiB,
      downloaded: 392 * MiB,
      speed: 2.1 * MiB,
      status: "downloading",
    },
    {
      kind: "file",
      name: "nebula-observation.tif",
      path: "photographs/nebula-observation.tif",
      size: 320 * MiB,
      downloaded: 91 * MiB,
      speed: 1.3 * MiB,
      status: "downloading",
    },
    {
      kind: "file",
      name: "solar-eclipse.tif",
      path: "photographs/solar-eclipse.tif",
      size: 210 * MiB,
      downloaded: 0,
      speed: 0,
      status: "queued",
    },
  ],
};
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
let releaseStartup;
const startupGate = new Promise((resolve) => {
  releaseStartup = resolve;
});
try {
  await page.route("**/api/**", async (route) => {
    const url = new URL(route.request().url());
    let body;
    if (url.pathname === "/api/auth") {
      await startupGate;
      body = { authenticated: true, mode: "dsm" };
    } else if (url.pathname === "/api/settings")
      body = {
        download_dir: "/volume1/Download/Archives",
        destination_locked: true,
        connections: 5,
        speed_limit_kib: 0,
        retries: 4,
        verify_checksums: true,
        language: "en",
        version: "0.2.0",
        disk_reserve_mib: 1024,
        notifications: true,
        timezone: "CEST",
        storage: { free: 3.7 * 1024 ** 4, total: 8 * 1024 ** 4 },
      };
    else if (url.pathname === "/api/jobs")
      body = {
        jobs,
        history: {
          values: Array.from(
            { length: 120 },
            (_, i) => (2.1 + Math.sin(i / 10) * 0.7 + i / 120) * MiB,
          ),
          period_seconds: 30,
          window_seconds: 3600,
        },
      };
    else if (url.pathname.endsWith("/report")) {
      const lines = reportText.trimEnd().split("\n");
      const offset = Number(url.searchParams.get("offset") || 0);
      body = {
        filename: "ArchiveStation-report-demo-space.txt",
        content: lines.slice(offset, offset + 200).join("\n"),
        total: lines.length,
        offset,
      };
    } else if (url.pathname.endsWith("/activity")) {
      const files = tree.photographs.map((row) => ({ ...row, name: row.path }));
      body = {
        active: files.filter((row) => row.status === "downloading"),
        queued: files.filter((row) => row.status === "queued"),
        errors: [],
        counts: { downloading: 2, queued: 1, completed: 173, error: 0 },
      };
    } else if (url.pathname.endsWith("/tree")) {
      const children = tree[url.searchParams.get("prefix") || ""] || [];
      body = { children, total: children.length };
    } else throw new Error(`Unexpected screenshot request: ${url.pathname}`);
    await route.fulfill({ json: body });
  });
  await page.goto(base, { waitUntil: "domcontentloaded" });
  await page
    .locator("#startup-status")
    .filter({ hasText: "Loading Archive Station" })
    .waitFor();
  assert.ok(await page.locator("#application").isHidden());
  await page.screenshot({ path: new URL("startup.png", out).pathname });
  releaseStartup();
  await page.getByText("lunar-surface.tif", { exact: true }).waitFor();
  await page.screenshot({ path: new URL("downloads.png", out).pathname });
  await page.locator("#history-panel summary").click();
  await page.screenshot({ path: new URL("history.png", out).pathname });
  await page.locator("#history-panel summary").click();
  await page.locator('[data-report-job="demo-space"]').click();
  await page
    .locator("#report-text")
    .filter({ hasText: "ERROR HISTORY" })
    .waitFor();
  await page.locator("#report-text").evaluate((element) => {
    const index = element.textContent
      .split("\n")
      .findIndex((line) => line === "ERROR HISTORY");
    element.parentElement.scrollTop =
      (index - 1) * parseFloat(getComputedStyle(element).lineHeight) +
      parseFloat(getComputedStyle(element.parentElement).paddingTop);
  });
  await page
    .locator("#report-dialog")
    .screenshot({ path: new URL("report.png", out).pathname });
  await page.locator("#report-dialog [data-close]").first().click();
  await page.getByRole("button", { name: "Folders", exact: true }).click();
  await page
    .getByRole("button", { name: "Expand photographs", exact: true })
    .click();
  await page.getByText("lunar-surface.tif", { exact: true }).waitFor();
  await page.screenshot({ path: new URL("folders.png", out).pathname });
  await page.locator('[data-file-view="activity"]').click();
  await page.getByText("lunar-surface.tif", { exact: true }).waitFor();
  await page.locator("#settings-open").click();
  await page.locator("#setting-language").selectOption("en");
  await page.screenshot({ path: new URL("settings.png", out).pathname });
  assert.deepEqual(errors, []);
  console.log("README screenshots generated from isolated demo data.");
} finally {
  await browser.close();
}
