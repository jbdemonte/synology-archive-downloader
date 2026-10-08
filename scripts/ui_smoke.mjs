import assert from "node:assert/strict";
import { chromium } from "playwright";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({
  locale: "fr-FR",
  viewport: { width: 1440, height: 1050 },
});
const errors = [];
page.on("pageerror", (error) => errors.push(error.message));
try {
  const base = process.env.TEST_URL || "http://127.0.0.1:8275";
  const previous = await (await page.request.get(base + "/api/jobs")).json();
  for (const job of previous.jobs) {
    if (["demo-one", "demo-two"].includes(job.identifier)) {
      await page.request.post(base + `/api/jobs/${job.id}/remove`, {
        data: {},
      });
    }
  }
  await page.goto(process.env.TEST_URL || "http://127.0.0.1:8275");
  await page.getByRole("heading", { name: "Transferts" }).waitFor();
  await page
    .getByRole("button", { name: "Ajouter des URL", exact: false })
    .click();
  await page
    .getByLabel("URL Archive.org", { exact: false })
    .fill(
      "https://archive.org/download/demo-one\nhttps://archive.org/details/demo-two",
    );
  await page.getByLabel("Ajouter en pause", { exact: false }).check();
  await page.getByRole("button", { name: "Analyser les URL" }).click();
  await page.getByRole("button", { name: "Ajouter 2 tâches" }).click();
  await page
    .getByRole("button", { name: "Déplier demo-one", exact: true })
    .waitFor();
  await page
    .getByRole("button", { name: "Déplier demo-one", exact: true })
    .click();
  await page.getByRole("button", { name: "Arborescence", exact: true }).click();
  await page.getByRole("button", { name: "Déplier roms", exact: true }).click();
  await page.getByText("game-000.zip", { exact: true }).waitFor();
  assert.equal(await page.locator(".job-row").count(), 2);
  assert.ok((await page.locator(".child-row").count()) >= 100);
  await page.getByRole("button", { name: "Suivants →" }).click();
  await page.getByText("game-100.zip", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Paramètres", exact: false }).click();
  await page.locator("#settings-dialog").waitFor({ state: "visible" });
  await page.locator("#setting-notifications").uncheck();
  await page.getByLabel("Téléchargements simultanés").fill("2");
  await page.getByLabel("Limite globale (Kio/s)").fill("1024");
  await page.getByRole("button", { name: "Enregistrer" }).click();
  await page.getByText("Paramètres enregistrés.", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Paramètres", exact: false }).click();
  assert.equal(
    await page.getByLabel("Téléchargements simultanés").inputValue(),
    "2",
  );
  await page.locator("#settings-dialog").waitFor({ state: "visible" });
  assert.equal(
    (await (await page.request.get(base + "/api/settings")).json())
      .notifications,
    false,
  );
  assert.equal(await page.locator("#setting-notifications").isChecked(), false);
  await page.getByRole("button", { name: "Sélectionner…" }).click();
  await page.getByRole("heading", { name: "Choisir un dossier" }).waitFor();
  await page
    .locator('#folder-dialog [data-close="folder-dialog"]')
    .first()
    .click();
  await page
    .locator('#settings-dialog [data-close="settings-dialog"]')
    .first()
    .click();
  await page.locator(".table-scroll").evaluate((el) => {
    el.scrollTop = 0;
  });
  await page.locator("#cancel").click();
  await page
    .getByRole("button", { name: "Annuler le téléchargement", exact: true })
    .click();
  await page
    .locator(".job-row[data-job]")
    .filter({ hasText: "demo-one" })
    .getByText("Annulé", { exact: true })
    .waitFor();
  await page.getByRole("button", { name: "Reprendre", exact: false }).click();
  await page.locator("#pause").click();
  await page
    .locator(".job-row[data-job]")
    .filter({ hasText: "demo-one" })
    .getByText("En pause", { exact: true })
    .waitFor();
  await page.screenshot({
    path: "/tmp/archive-station-desktop.png",
    fullPage: true,
  });
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "/tmp/archive-station-mobile.png",
    fullPage: true,
  });
  assert.equal(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
    false,
  );
  assert.deepEqual(errors, []);
  console.log(
    "UI smoke passed: multi-URL, hierarchy, pagination, settings, folder selector, mobile layout.",
  );
} finally {
  await browser.close();
}
