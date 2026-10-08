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
  const order = await page
    .locator("#setting-language option")
    .evaluateAll((options) =>
      options
        .slice(1)
        .map((option) =>
          option.textContent.slice(option.textContent.indexOf(" ") + 1),
        ),
    );
  assert.deepEqual(
    order,
    [...order].sort((a, b) =>
      a.localeCompare(b, "fr", { sensitivity: "base" }),
    ),
  );
  assert.equal(
    await page
      .locator("#setting-language option")
      .first()
      .getAttribute("value"),
    "auto",
  );
  assert.equal(
    await page.locator("#browse-settings").innerText(),
    "Sélectionner…",
  );
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
      catalog["Transferts"],
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
    for (const width of [1360, 1000, 800]) {
      await page.setViewportSize({ width, height: 720 });
      const overflow = await page.locator(".nav-item").evaluateAll((buttons) =>
        buttons.flatMap((button) => {
          const label = button.querySelector("[data-i18n]");
          const count = button.querySelector("b");
          const original = count.textContent;
          count.textContent = "42798";
          const range = document.createRange();
          range.selectNodeContents(label);
          const bounds = button.getBoundingClientRect();
          const countBounds = count.getBoundingClientRect();
          const clipped = [...range.getClientRects()].some(
            (r) =>
              r.left < bounds.left ||
              r.right > countBounds.left ||
              r.top < bounds.top ||
              r.bottom > bounds.bottom,
          );
          count.textContent = original;
          return clipped ? [label.textContent] : [];
        }),
      );
      assert.deepEqual(
        overflow,
        [],
        `Navigation labels must fit in ${code} at ${width}px`,
      );
      const overlap = await page.locator(".page-title").evaluate((element) => {
        const title = element.querySelector("h1").getBoundingClientRect();
        const buttons = element
          .querySelector(".page-actions")
          .getBoundingClientRect();
        return title.right > buttons.left || buttons.right > innerWidth;
      });
      assert.equal(
        overlap,
        false,
        `Title and buttons must fit in ${code} at ${width}px`,
      );
    }
    // Every supported plural category must interpolate numeric counts safely.
    const counts = await page.evaluate(() =>
      [0, 1, 2, 5, 21, 101, 1000].map((count) => ({
        tasks: ArchiveI18n.t("{count} tâches", { count }),
        files: ArchiveI18n.t("{completed} / {count} fichiers", {
          completed: 1,
          count,
        }),
        incidents: ArchiveI18n.t("{count} incidents consignés", { count }),
      })),
    );
    for (const row of counts)
      for (const text of Object.values(row)) {
        assert.ok(
          !/[{}]|undefined|\[object Object\]/.test(text),
          `${code}: ${text}`,
        );
      }
    const expected = {
      en: ["0 tasks", "1 task", "2 tasks", "5 tasks", "21 tasks"],
      fr: ["0 tâche", "1 tâche", "2 tâches", "5 tâches", "21 tâches"],
      pl: ["0 zadań", "1 zadanie", "2 zadania", "5 zadań", "21 zadań"],
      ru: ["0 задач", "1 задача", "2 задачи", "5 задач", "21 задача"],
      uk: ["0 завдань", "1 завдання", "2 завдання", "5 завдань", "21 завдання"],
      cs: ["0 úkolů", "1 úkol", "2 úkoly", "5 úkolů", "21 úkolů"],
    }[code];
    if (expected)
      assert.deepEqual(
        counts.slice(0, 5).map((row) => row.tasks),
        expected,
      );
    if (code === "ru") {
      const markup = await page.evaluate(() =>
        activityRows(
          { id: "example" },
          {
            active: [],
            errors: [],
            queued: [],
            counts: { downloading: 0, error: 0, queued: 2000 },
          },
        ),
      );
      assert.ok(
        markup.includes("0 / 2&nbsp;000 файлов") ||
          markup.includes("0 / 2 000 файлов") ||
          markup.includes("0 / 2 000 файлов"),
      );
    }
    if (code === "en") {
      assert.equal(counts[1].files, "1 / 1 file");
      assert.equal(counts[1].incidents, "1 recorded incident");
      assert.equal(
        await page.evaluate(() =>
          ArchiveI18n.t("Transfert interrompu avant la fin du fichier."),
        ),
        "Transfer interrupted before the end of the file.",
      );
      assert.equal(
        await page.evaluate(() =>
          ArchiveI18n.t("Réponse HTTP inattendue : 503"),
        ),
        "Unexpected HTTP response: 503",
      );
      assert.equal(await page.evaluate(() => bytes(0.5)), "1 B");
    }
    await page.setViewportSize({ width: 1000, height: 720 });
  }
  await page.reload();
  await page.waitForFunction(
    (code) => document.documentElement.lang === code,
    languages.at(-1),
  );
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
