import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1000, height: 720 },
});
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.goto(base);
  await page.locator("#settings-open").click();
  const languages = await page
    .locator("#setting-language option")
    .evaluateAll((options) =>
      options.map((option) => option.value).filter((code) => code !== "auto"),
    );
  assert.equal(languages.length, 27);
  await page.locator("#settings-dialog [data-close]").first().click();
  for (const code of languages) {
    const catalog = JSON.parse(
      await readFile(
        new URL(
          `../src/archive_station/static/locales/${code}.json`,
          import.meta.url,
        ),
      ),
    );
    await page.locator("#settings-open").click();
    await page.locator("#setting-language").selectOption(code);
    await page.locator('#settings-form button[type="submit"]').click();
    await page.waitForFunction(
      (language) => document.documentElement.lang === language,
      code,
    );
    assert.ok(
      (await page.locator("#settings-open").innerText()).includes(
        catalog["Paramètres"],
      ),
    );
    assert.equal(
      await page.locator("#view-title").innerText(),
      catalog["Tous les téléchargements"],
    );
    assert.equal(
      (await (await page.request.get(base + "/api/settings")).json()).language,
      code,
    );
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
    );
  }
  await page.reload();
  await page.waitForFunction(() => document.documentElement.lang === "vi");
  await page.locator("#settings-open").click();
  await page.locator("#setting-language").selectOption("auto");
  await page.locator('#settings-form button[type="submit"]').click();
  await page.waitForFunction(() => document.documentElement.lang === "fr");
  assert.deepEqual(errors, []);
  console.log(
    "Languages passed: all 27 catalogs, settings persistence, reload, browser fallback and layout.",
  );
} finally {
  await browser.close();
}
