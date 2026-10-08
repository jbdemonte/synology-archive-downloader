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
page.on("pageerror", (e) => errors.push(e.message));
const ids = [];
let longDestination = false;
try {
  for (const url of ["demo-one", "demo-two"])
    ids.push(
      (
        await (
          await page.request.post(base + "/api/jobs", {
            data: { url, paused: true },
          })
        ).json()
      ).id,
    );
  await page.route("**/api/jobs", async (route) => {
    const result = await (await route.fetch()).json();
    const held = result.jobs.find((j) => j.id === ids[0]);
    if (held) {
      held.hold_reason = "disk";
      if (longDestination)
        held.destination =
          "/volume1/" + Array(12).fill("Folder".repeat(16)).join("/");
    }
    await route.fulfill({ json: result });
  });
  await page.goto(base);
  const menu = page.locator("#task-menu");
  const more = (id) => page.locator(`[data-menu-job="${id}"]`);

  // The former details panel only reported a disk hold; the row now shows it.
  await page
    .locator(`.job-row[data-job="${ids[0]}"] .status-note`)
    .filter({ hasText: "Espace disque insuffisant." })
    .waitFor();

  // Menu actions target their own row and leave the toolbar selection alone.
  await page.locator(`[data-select-job="${ids[0]}"]`).check();
  await more(ids[1]).click();
  await menu.waitFor();
  assert.equal(await more(ids[1]).getAttribute("aria-expanded"), "true");
  assert.match(await page.locator("#menu-destination").innerText(), /demo-two/);
  assert.ok(await page.locator("#open-folder").isHidden(), "DSM-only action");
  assert.equal(
    await page.locator('[data-job-priority="0"]').getAttribute("aria-checked"),
    "true",
  );

  // Rows are rebuilt by polling; the menu stays open and anchored.
  await page.waitForTimeout(2000);
  assert.ok(await menu.isVisible());
  const box = await menu.boundingBox();
  assert.ok(
    box.y + box.height <= 650 && box.x >= 0 && box.x + box.width <= 1100,
  );

  await page.locator('[data-job-priority="1"]').click();
  await menu.waitFor({ state: "hidden" });
  await page.waitForFunction(
    (id) => document.querySelector(".job-row").dataset.job === id,
    ids[1],
  );
  const jobs = (await (await page.request.get(base + "/api/jobs")).json()).jobs;
  assert.equal(jobs.find((j) => j.id === ids[1]).priority, 1);
  assert.ok(await page.locator(`[data-select-job="${ids[0]}"]`).isChecked());

  // Keyboard: Enter opens and focuses the first item, arrows move, Escape
  // closes and returns focus to the row button.
  await more(ids[1]).focus();
  await page.keyboard.press("Enter");
  await menu.waitFor();
  assert.equal(
    await page.evaluate(() => document.activeElement.dataset.jobPriority),
    "1",
  );
  await page.keyboard.press("ArrowDown");
  assert.equal(
    await page.evaluate(() => document.activeElement.dataset.jobPriority),
    "0",
  );
  await page.keyboard.press("End");
  assert.equal(
    await page.evaluate(() => document.activeElement.id),
    "read-report",
  );
  await page.keyboard.press("Escape");
  await menu.waitFor({ state: "hidden" });
  assert.equal(
    await page.evaluate(() => document.activeElement.dataset.menuJob),
    ids[1],
  );

  // A second click on the same button, or a click elsewhere, closes it.
  await more(ids[0]).click();
  await menu.waitFor();
  await more(ids[0]).click();
  await menu.waitFor({ state: "hidden" });
  await more(ids[0]).click();
  await menu.waitFor();
  await page.locator("#list-count").click();
  await menu.waitFor({ state: "hidden" });

  // Escape also dismisses mouse-opened menus, whose focus stays on the anchor.
  await more(ids[0]).click();
  await menu.waitFor();
  await page.keyboard.press("Escape");
  await menu.waitFor({ state: "hidden" });
  assert.equal(
    await page.evaluate(() => document.activeElement.dataset.menuJob),
    ids[0],
  );

  // Opening a dialog from the menu uses that row, not the selected one.
  await more(ids[0]).click();
  await page.locator("#read-report").click();
  await page.locator("#report-filename").filter({ hasText: ids[0] }).waitFor();
  await page.locator("#report-dialog [data-close]").first().click();

  // Long valid paths must not push menu actions outside a short DSM window.
  longDestination = true;
  await page.setViewportSize({ width: 800, height: 460 });
  await page.reload();
  await more(ids[0]).click();
  await menu.waitFor();
  const compact = await menu.boundingBox();
  assert.ok(compact.y >= 0 && compact.y + compact.height <= 460);
  assert.ok(compact.x >= 0 && compact.x + compact.width <= 800);
  await page.keyboard.press("Escape");
  await more(ids[0]).press("Enter");
  await page.keyboard.press("End");
  assert.equal(
    await page.evaluate(() => document.activeElement.id),
    "read-report",
  );
  const lastItem = await page.locator("#read-report").boundingBox();
  assert.ok(lastItem.y >= 0 && lastItem.y + lastItem.height <= 460);
  await page.locator("#read-report").click();
  await page.locator("#report-filename").filter({ hasText: ids[0] }).waitFor();
  await page.locator("#report-dialog [data-close]").first().click();

  // Actual table scrolling still dismisses the menu after it has opened.
  await more(ids[0]).click();
  await menu.waitFor();
  const moved = await page.locator(".table-scroll").evaluate((table) => {
    const before = table.scrollTop;
    table.scrollTop = before > 0 ? 0 : table.scrollHeight;
    return table.scrollTop !== before;
  });
  assert.ok(moved, "The compact table should actually scroll");
  await menu.waitFor({ state: "hidden" });
  assert.deepEqual(errors, []);
  console.log(
    "Task menu passed: row-scoped actions, disk hold note, polling persistence, keyboard/mouse dismissal and compact-window scrolling.",
  );
} finally {
  for (const id of ids)
    await page.request.post(base + `/api/jobs/${id}/remove`, { data: {} });
  await browser.close();
}
