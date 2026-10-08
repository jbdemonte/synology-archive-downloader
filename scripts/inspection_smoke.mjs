import assert from "node:assert/strict";
import { chromium } from "playwright";

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1000, height: 580 },
});
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const errors = [];
const requests = [];
let deliver;
function nextRequest() {
  return new Promise((resolve) => {
    deliver = resolve;
  });
}
async function reply(route, identifier) {
  await route.fulfill({
    json: {
      identifier,
      file_count: 2,
      total_size: 100,
      sample: ["file.zip"],
      unknown_sizes: 0,
      private_files: 0,
    },
  });
}
async function checkLocked(locked) {
  for (const id of [
    "urls",
    "file-mode",
    "file-pattern",
    "job-destination",
    "start-paused",
    "browse-job",
    "inspect-button",
    "create-button",
  ])
    assert.equal(await page.locator(`#${id}`).isDisabled(), locked, id);
  assert.equal(await page.locator("#inspect-status").isVisible(), locked);
  assert.equal(
    await page.locator("#add-form .dialog-body").getAttribute("aria-busy"),
    String(locked),
  );
}
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.goto(base);
  await page.locator("#add-open").click();
  await page
    .locator("#urls")
    .fill(
      "https://archive.org/details/demo-one\nhttps://archive.org/download/demo-two",
    );
  await page.locator("#file-pattern").fill("*.zip");
  await page.route("**/api/inspect", (route) => {
    requests.push(route.request().postDataJSON());
    deliver(route);
  });
  let arrived = nextRequest();
  await page.locator("#inspect-button").click();
  const first = await arrived;
  await checkLocked(true);
  assert.equal(
    await page.locator("#inspect-progress").textContent(),
    "Analyse de l’URL 1 sur 2…",
  );
  await page.locator("#add-form").dispatchEvent("submit");
  assert.equal(
    requests.length,
    1,
    "A duplicate submission must not start another analysis",
  );
  await page.keyboard.type("texte ajouté");
  assert.equal(
    await page.locator("#urls").inputValue(),
    "https://archive.org/details/demo-one\nhttps://archive.org/download/demo-two",
  );
  assert.ok(
    await page.locator("#inspect-status").evaluate((el) => {
      const box = el.getBoundingClientRect();
      return box.top >= 0 && box.bottom <= innerHeight;
    }),
  );
  await page.screenshot({ path: "/tmp/archive-station-inspection.png" });
  arrived = nextRequest();
  await reply(first, "demo-one");
  const second = await arrived;
  assert.equal(
    await page.locator("#inspect-progress").textContent(),
    "Analyse de l’URL 2 sur 2…",
  );
  await checkLocked(true);
  assert.ok(requests.every((request) => request.pattern === "*.zip"));
  await reply(second, "demo-two");
  await page.locator("#inspect-status").waitFor({ state: "hidden" });
  await checkLocked(false);
  assert.equal(
    await page.locator("#create-button").textContent(),
    "Ajouter 2 tâches",
  );

  // Real validation error: restore every field and allow correcting the URL.
  await page.unroute("**/api/inspect");
  await page
    .locator("#urls")
    .fill("https://archive.org/details/MAME_0.278_ROMs_non-merged_2025her un");
  await page.locator("#inspect-button").click();
  await page
    .locator("#add-error")
    .filter({ hasText: "Identifiant Archive.org invalide" })
    .waitFor();
  await checkLocked(false);
  assert.equal(await page.locator("#create-button").isVisible(), false);

  // A transport failure also unlocks the form.
  await page.route("**/api/inspect", (route) => route.abort("failed"));
  await page.locator("#urls").fill("https://archive.org/details/demo-one");
  await page.locator("#inspect-button").click();
  await page
    .locator("#add-error")
    .filter({ hasText: "Failed to fetch" })
    .waitFor();
  await checkLocked(false);
  await page.unroute("**/api/inspect");

  // Cancel and reopen while the old response is pending: discard stale results.
  await page.route("**/api/inspect", (route) => {
    requests.push(route.request().postDataJSON());
    deliver(route);
  });
  await page
    .locator("#urls")
    .fill(
      "https://archive.org/details/demo-one\nhttps://archive.org/details/demo-two",
    );
  arrived = nextRequest();
  await page.locator("#inspect-button").click();
  const cancelled = await arrived;
  const requestCount = requests.length;
  await page.locator("#add-dialog .dialog-footer [data-close]").click();
  await page.locator("#add-open").click();
  await checkLocked(false);
  await page.locator("#urls").fill("https://archive.org/details/demo-two");
  arrived = nextRequest();
  await page.locator("#inspect-button").click();
  const current = await arrived;
  await reply(cancelled, "stale-result");
  await checkLocked(true);
  await reply(current, "demo-two");
  await page.locator("#inspect-status").waitFor({ state: "hidden" });
  assert.equal(await page.locator(".preview-item").count(), 1);
  assert.ok(
    (await page.locator(".preview-item").textContent()).includes("demo-two"),
  );
  assert.equal(requests.length, requestCount + 1);
  await checkLocked(false);

  // Escape cancels too, leaving the next form ready to use.
  await page.locator("#urls").fill("https://archive.org/details/demo-one");
  arrived = nextRequest();
  await page.locator("#inspect-button").click();
  const escaped = await arrived;
  await page.keyboard.press("Escape");
  await page.locator("#add-dialog").waitFor({ state: "hidden" });
  await reply(escaped, "stale-result");
  await page.locator("#add-open").click();
  await checkLocked(false);
  assert.equal(await page.locator(".preview-item").count(), 0);
  assert.deepEqual(errors, []);
  console.log(
    "Analysis UI passed: loading indicator, locked fields, multi-URL progress, validation/network recovery, cancellation and stale responses.",
  );
} finally {
  await browser.close();
}
