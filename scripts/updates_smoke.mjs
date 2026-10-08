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
const releaseURL =
  "https://github.com/jbdemonte/synology-archive-downloader/releases/tag/v0.2.0-8";
let checks = 0;
let update = { state: "disabled", current_version: "0.2.0-7", retry_after: 0 };
try {
  await page.request.post(base + "/api/settings", {
    data: { language: "fr", check_updates: false, update_prereleases: false },
  });
  for (const endpoint of ["settings", "jobs"]) {
    await page.route(`${base}/api/${endpoint}`, async (route) => {
      if (route.request().method() !== "GET") return route.continue();
      const response = await route.fetch();
      const body = await response.json();
      await route.fulfill({ json: { ...body, updates: update } });
    });
  }
  await page.route(`${base}/api/updates/check`, async (route) => {
    checks++;
    update = { ...update, state: "checking" };
    await route.fulfill({ status: 202, json: update });
  });
  await page.goto(base);
  await page.locator("#settings-open").click();
  await page.locator("#update-settings summary").click();
  assert.match(
    await page.locator("#update-status").textContent(),
    /désactivée/,
  );
  await page.locator("#update-check").click();
  await page.waitForFunction(
    () => document.getElementById("update-check").disabled,
  );
  assert.equal(checks, 1);
  assert.equal(
    await page.locator("#update-status").getAttribute("aria-busy"),
    "true",
  );
  update = {
    ...update,
    state: "available",
    latest_version: "0.2.0-8",
    release_url: releaseURL,
    checked_at: Date.now() / 1000,
    retry_after: 60,
  };
  await page.locator('#update-status[data-state="available"]').waitFor();
  assert.match(await page.locator("#update-check").textContent(), /60 s/);
  assert.equal(
    await page.locator("#update-release").getAttribute("href"),
    releaseURL,
  );
  assert.equal(
    await page.locator("#update-release").getAttribute("target"),
    "_blank",
  );
  assert.equal(
    await page.locator("#update-available").getAttribute("href"),
    releaseURL,
  );
  await page.locator("#setting-check-updates").check();
  await page.locator("#setting-update-prereleases").check();
  assert.match(
    await page.locator("#update-status").textContent(),
    /Enregistrer/,
  );
  assert.ok(await page.locator("#update-check").isDisabled());
  await page.locator('#settings-form [type="submit"]').click();
  await page.locator("#settings-dialog").waitFor({ state: "hidden" });
  const saved = await (await page.request.get(base + "/api/settings")).json();
  assert.equal(saved.check_updates, true);
  assert.equal(saved.update_prereleases, true);
  assert.match(
    await page.locator("#update-available").textContent(),
    /0\.2\.0-8/,
  );
  await page.locator("#settings-open").click();
  await page.locator("#settings-dialog").waitFor();
  assert.ok(await page.locator("#setting-check-updates").isChecked());
  for (const [status, text] of [
    ["current", "à jour"],
    ["none", "Aucune version publique"],
    ["error", "impossible"],
  ]) {
    update = { ...update, state: status, retry_after: 0, release_url: null };
    await page.locator(`#update-status[data-state="${status}"]`).waitFor();
    assert.match(
      await page.locator("#update-status").textContent(),
      new RegExp(text),
    );
    assert.ok(await page.locator("#update-release").isHidden());
    assert.ok(await page.locator("#update-available").isHidden());
    assert.ok(await page.locator("#update-check").isEnabled());
  }
  update = {
    ...update,
    state: "available",
    latest_version: "0.2.0-8",
    release_url: "javascript:alert(1)",
  };
  await page.locator('#update-status[data-state="available"]').waitFor();
  assert.ok(await page.locator("#update-release").isHidden());
  assert.equal(
    await page.locator("#update-release").getAttribute("href"),
    null,
  );
  update.release_url = releaseURL;
  await page.setViewportSize({ width: 800, height: 460 });
  await page.locator("#update-release").waitFor();
  await page.locator("#update-check").scrollIntoViewIfNeeded();
  const overflow = await page
    .locator("#settings-dialog")
    .evaluate((dialog) => dialog.scrollWidth > dialog.clientWidth);
  assert.equal(overflow, false);
  const check = await page.locator("#update-check").boundingBox();
  assert.ok(check.y >= 0 && check.y + check.height <= 460);
  assert.equal(
    checks,
    1,
    "Opening and polling the interface must not trigger network checks",
  );
  console.log(
    "Updates UI passed: opt-in, pending settings, checking state, errors, safe release links and small windows.",
  );
} finally {
  await page.request.post(base + "/api/settings", {
    data: { check_updates: false, update_prereleases: false },
  });
  await browser.close();
}
