import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { chromium } from "playwright";

// Exercise the embedded frontend with a simulated DSM session. No real DSM
// credentials are used; gateway validation has separate Python tests.
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const root = new URL("../", import.meta.url);
const config = JSON.parse(
  await readFile(new URL("packaging/synology/ui/config", root)),
);
const appURL = config[".url"]["com.archivestation.app"].url;
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const page = await browser.newPage({ viewport: { width: 1440, height: 1050 } });
const errors = [];
let expired = false;
let authenticatedCalls = 0;
let serviceUnavailable = false;
page.on("pageerror", (error) => errors.push(error.message));
try {
  await page.route(`${base}/desktop-test`, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<!doctype html><html><head>
      <style>body { margin: 8px; font: 19px serif; } button { padding: 2px; border-radius: 0; }</style>
      <link rel="stylesheet" href="/webman/3rdparty/ArchiveStation/style.css">
      <script>window.SYNO={SDS:{Session:{SynoToken:"test-dsm-token",lang:"fre"}}};</script>
      <script>
      window.fileStationLaunches = [];
      window.SYNO.SDS.AppLaunch = (name, options) => fileStationLaunches.push({name, options});
      window.layoutSettings = {};
      window.resizes = [];
      window.nativeWindow = {
        iframeId: "archive-frame", jsConfig: {jsID: "com.archivestation.app"},
        appInstance: {
          getUserSettings: key => window.layoutSettings[key],
          setUserSettings: (key, value) => { window.layoutSettings[key] = value; }
        },
        getSize: () => ({width: 1000, height: 1050}),
        setSize: (width, height) => {
          window.resizes.push({width, height});
          const frame = document.getElementById("archive-frame");
          frame.style.width = width + "px"; frame.style.height = height + "px";
        },
        onHandlerResize: () => { window.savedGeometry = true; }
      };
      window.Ext = {getCmp: id => id === "native-window" ? window.nativeWindow : null};
      window.SYNO.SDS.WindowMgr = {centerWindow: () => { window.centered = true; }};
      </script></head><body><button id="dsm-button">Centre de paquets</button>
      <div id="native-window"><iframe id="archive-frame" title="Archive Station" src="${appURL}" style="display:block;width:1200px;height:800px;border:0"></iframe></div>
      </body></html>`,
    }),
  );
  await page.route(
    `${base}/webman/3rdparty/ArchiveStation/**`,
    async (route) => {
      const url = new URL(route.request().url());
      if (url.pathname.endsWith("/gateway.cgi")) {
        assert.equal(
          route.request().headers()["x-syno-token"],
          "test-dsm-token",
        );
        const api = url.searchParams.get("route");
        assert.ok(api.startsWith("/api/"));
        assert.ok(
          !["/api/login", "/api/password", "/api/logout"].includes(api),
        );
        if (expired) {
          await route.fulfill({
            status: 401,
            json: { mode: "dsm", error: "Session DSM expirée." },
          });
        } else if (api === "/api/auth") {
          await route.fulfill({
            json: { mode: "dsm", authenticated: true, configured: true },
          });
        } else if (api.endsWith("/location")) {
          await route.fulfill({
            json: { file_station_path: "/Download/Jeux & collections/été" },
          });
        } else if (api === "/api/jobs" && serviceUnavailable) {
          await route.fulfill({
            json: { _http_status: 503, error: "Le service est indisponible." },
          });
        } else if (
          api.startsWith("/api/folders") &&
          route.request().method() === "GET"
        ) {
          if (
            new URL(base + api).searchParams.get("path") === "/test-read-only"
          ) {
            await route.fulfill({
              json: {
                path: "/test-read-only",
                parent: "",
                writable: false,
                folders: [],
              },
            });
            return;
          }
          if (
            new URL(base + api).searchParams.get("path") ===
            "/test-permission-denied"
          ) {
            await route.fulfill({
              json: {
                _http_status: 403,
                error: "Accès au dossier refusé. Vérifiez les permissions DSM.",
              },
            });
            return;
          }
          const listing = await (await route.fetch({ url: base + api })).json();
          listing.folders.push({
            name: "Download (sans droits)",
            path: "/volume1/Download",
            readable: false,
            writable: false,
          });
          listing.folders.push({
            name: "Roms (lecture seule)",
            path: "/test-read-only",
            readable: true,
            writable: false,
          });
          listing.folders.push({
            name: "Droits retirés pendant la navigation",
            path: "/test-permission-denied",
            readable: true,
            writable: true,
          });
          await route.fulfill({ json: listing });
        } else {
          authenticatedCalls++;
          await route.fulfill({
            response: await route.fetch({ url: base + api }),
          });
        }
        return;
      }
      if (url.pathname.includes("/locales/")) {
        await route.fulfill({
          contentType: "application/json",
          body: await readFile(
            new URL(
              "src/archive_station/static/locales/" +
                url.pathname.split("/").pop(),
              root,
            ),
          ),
        });
        return;
      }
      const name = url.pathname.split("/").pop();
      const types = {
        "index.html": "text/html",
        "style.css": "text/css",
        "app.js": "application/javascript",
        "i18n.js": "application/javascript",
        "icon.png": "image/png",
      };
      assert.ok(name in types);
      const path = url.pathname.includes("/web/")
        ? `src/archive_station/static/${name}`
        : "packaging/synology/ui/style.css";
      await route.fulfill({
        contentType: types[name],
        body: await readFile(new URL(path, root)),
      });
    },
  );
  if (!(await (await page.request.get(base + "/api/jobs")).json()).jobs.length)
    await page.request.post(base + "/api/jobs", {
      data: { url: "demo-one", paused: true },
    });
  await page.goto(base + "/desktop-test");
  const app = page.frameLocator('iframe[title="Archive Station"]');
  await app.getByRole("heading", { name: "Transferts" }).waitFor();
  await app.locator("[data-menu-job]").first().click();
  await app.locator("#open-folder").click();
  await page.waitForFunction(() => fileStationLaunches.length === 1);
  assert.deepEqual(await page.evaluate(() => fileStationLaunches), [
    {
      name: "SYNO.SDS.App.FileStation3.Instance",
      options: { opendir: "/Download/Jeux & collections/été" },
    },
  ]);
  assert.equal(
    page.context().pages().length,
    1,
    "File Station opens inside DSM",
  );
  assert.deepEqual(await page.evaluate(() => resizes), [
    { width: 1360, height: 840 },
  ]);
  assert.equal(await page.evaluate(() => savedGeometry && centered), true);
  const nativeFrame = page
    .frames()
    .find((frame) => frame.url().includes("/webman/"));
  await nativeFrame.goto(base + appURL);
  await app.locator("#application").waitFor({ state: "visible" });
  assert.equal(
    await page.evaluate(() => resizes.length),
    1,
    "Manual geometry is preserved after migration",
  );
  await page.evaluate(() => {
    layoutSettings = {};
    nativeWindow.maximized = true;
  });
  await nativeFrame.goto(base + appURL);
  await app.locator("#application").waitFor({ state: "visible" });
  assert.equal(
    await page.evaluate(() => resizes.length),
    1,
    "Maximized windows must stay maximized",
  );
  assert.equal(
    (await (await page.request.get(base + "/api/settings")).json())
      .report_language,
    "fr",
    "Background reports remember the resolved DSM session language",
  );
  assert.equal(await app.locator("#logout").isVisible(), false);
  assert.equal(await app.locator("#password").isVisible(), false);
  assert.equal(await app.locator(".app-header").isVisible(), false);
  assert.equal(await app.locator("#connection").isVisible(), false);
  for (const [width, height] of [
    [1200, 710],
    [1000, 580],
    [800, 460],
    [390, 680],
  ]) {
    await page.locator('iframe[title="Archive Station"]').evaluate(
      (el, size) => {
        el.style.width = `${size[0]}px`;
        el.style.height = `${size[1]}px`;
      },
      [width, height],
    );
    const reachable = await app.locator("#settings-open").evaluate((el) => {
      const box = el.getBoundingClientRect();
      return (
        box.top >= 0 &&
        box.bottom <= window.innerHeight &&
        box.left >= 0 &&
        box.right <= window.innerWidth &&
        el.contains(
          document.elementFromPoint(
            box.left + box.width / 2,
            box.top + box.height / 2,
          ),
        )
      );
    });
    assert.ok(
      reachable,
      `Settings must be visible without scrolling at ${width}×${height}`,
    );
    const layout = await app.locator("main").evaluate((el) => ({
      overflow: el.scrollHeight > el.clientHeight + 1,
      tableHeight: document.querySelector(".table-scroll").clientHeight,
      documentOverflow:
        document.documentElement.scrollHeight > window.innerHeight + 1,
    }));
    assert.equal(
      layout.overflow,
      false,
      `Main content must fit ${width}×${height}`,
    );
    assert.equal(layout.documentOverflow, false);
    assert.ok(
      layout.tableHeight > 100,
      `File list remains usable at ${width}×${height}`,
    );
    await app.locator("#settings-open").click();
    await app.locator("#settings-dialog").waitFor({ state: "visible" });
    const selectStyle = await app
      .locator("#setting-language")
      .evaluate((el) => {
        const style = getComputedStyle(el);
        return {
          paddingRight: parseFloat(style.paddingRight),
          arrow: style.backgroundPosition,
        };
      });
    assert.ok(
      selectStyle.paddingRight >= 36,
      "Select text reserves space for the inset arrow",
    );
    assert.ok(selectStyle.arrow.includes("14px"));
    assert.equal(
      await app
        .locator('#settings-dialog button[type="submit"]')
        .evaluate(
          (el) =>
            getComputedStyle(el).fontSize ===
            getComputedStyle(el.querySelector("[data-i18n]")).fontSize,
        ),
      true,
      "Translated Save label retains normal button typography",
    );
    assert.ok(
      await app
        .locator('#settings-dialog button[type="submit"]')
        .evaluate((el) => {
          const box = el.getBoundingClientRect();
          return box.top >= 0 && box.bottom <= window.innerHeight;
        }),
      "Settings footer stays visible",
    );
    await app
      .locator('#settings-dialog [data-close="settings-dialog"]')
      .first()
      .click();
  }
  await page.locator('iframe[title="Archive Station"]').evaluate((el) => {
    el.style.width = "1200px";
    el.style.height = "710px";
  });
  await app.locator("#destination-edit").click();
  await app.locator("#settings-dialog").waitFor({ state: "visible" });
  const destination = app.locator("#setting-destination");
  assert.equal(
    await destination.evaluate((el) => el === document.activeElement),
    true,
  );
  await app.locator("#browse-settings").click();
  const lockedFolder = app.locator(
    '#folder-list [data-folder="/volume1/Download"]',
  );
  await lockedFolder.waitFor({ state: "visible" });
  assert.equal(await lockedFolder.isDisabled(), true);
  assert.match(await lockedFolder.textContent(), /Aucun accès · à autoriser/);
  const writableRoot = app
    .locator("#folder-list .folder-entry.writable")
    .first();
  const rootPath = await writableRoot.getAttribute("data-folder");
  assert.match(await writableRoot.textContent(), /Lecture\/écriture/);
  assert.match(
    await app
      .locator('#folder-list [data-folder="/test-read-only"]')
      .textContent(),
    /Lecture seule/,
  );
  assert.equal(
    await lockedFolder.evaluate((el) => getComputedStyle(el).backgroundColor),
    "rgb(255, 245, 229)",
  );
  await app.locator(".folder-permissions").scrollIntoViewIfNeeded();
  await page
    .locator('iframe[title="Archive Station"]')
    .screenshot({ path: "/tmp/archive-station-folder-permissions.png" });
  await app.locator('#folder-list [data-folder="/test-read-only"]').click();
  await app
    .locator("#folder-error")
    .filter({ hasText: "pas accessible en écriture" })
    .waitFor();
  assert.equal(await app.locator("#folder-new").isDisabled(), true);
  assert.equal(await app.locator("#folder-select").isDisabled(), true);
  await app.locator("#folder-up").click();
  await app
    .locator('#folder-list [data-folder="/test-permission-denied"]')
    .click();
  await app
    .getByText("Accès au dossier refusé. Vérifiez les permissions DSM.", {
      exact: true,
    })
    .waitFor();
  assert.equal(
    await app.getByText("Chargement des dossiers…", { exact: true }).count(),
    0,
  );
  await app
    .locator('#folder-dialog [data-close="folder-dialog"]')
    .first()
    .click();
  await app.locator("#browse-settings").click();
  await app.locator(`#folder-list [data-folder="${rootPath}"]`).click();
  const parentPath = await destination.inputValue();
  await app.locator(`#folder-list [data-folder="${parentPath}"]`).click();
  await app.locator("#folder-new").click();
  await app.getByLabel("Nom du nouveau dossier").fill("Mes archives");
  await app.locator("#folder-create-submit").click();
  const newDestination = parentPath + "/Mes archives";
  await app
    .locator("#folder-path")
    .filter({ hasText: newDestination })
    .waitFor();
  await app.locator("#folder-select").click();
  assert.equal(await destination.inputValue(), newDestination);
  await app.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await app.getByText("Paramètres enregistrés.", { exact: true }).waitFor();
  assert.equal(
    await app.locator("#destination-short").textContent(),
    newDestination,
  );
  assert.equal(
    (await (await page.request.get(base + "/api/settings")).json())
      .download_dir,
    newDestination,
  );
  await page
    .locator('iframe[title="Archive Station"]')
    .screenshot({ path: "/tmp/archive-station-settings-visible.png" });
  await app.getByRole("button", { name: "Paramètres", exact: false }).click();
  assert.equal(await app.locator("#setting-password").isVisible(), false);
  await app.getByRole("button", { name: "Enregistrer", exact: true }).click();
  await app.getByText("Paramètres enregistrés.", { exact: true }).waitFor();
  // The root stylesheet is automatically loaded by DSM, outside the iframe.
  // Its former global rules must not change the surrounding desktop.
  assert.deepEqual(
    await page.locator("body").evaluate((el) => ({
      margin: getComputedStyle(el).margin,
      fontSize: getComputedStyle(el).fontSize,
      boxSizing: getComputedStyle(el).boxSizing,
    })),
    { margin: "8px", fontSize: "19px", boxSizing: "content-box" },
  );
  assert.equal(
    await page
      .locator("#dsm-button")
      .evaluate((el) => getComputedStyle(el).borderRadius),
    "0px",
  );
  assert.ok(authenticatedCalls >= 3);
  // DSM's French session wins over this browser's English default. A manual
  // setting wins over DSM, survives reload, and can return to automatic.
  await app.locator("#settings-open").click();
  await app.locator("#setting-language").selectOption("en");
  await app.locator('#settings-form button[type="submit"]').click();
  await app.getByRole("heading", { name: "Transfers", exact: true }).waitFor();
  await page.reload();
  await app.getByRole("heading", { name: "Transfers", exact: true }).waitFor();
  await app.locator("#settings-open").click();
  await app.locator("#settings-dialog").waitFor({ state: "visible" });
  assert.equal(await app.locator("#setting-language").inputValue(), "en");
  await app.locator("#setting-language").selectOption("auto");
  await app.locator('#settings-form button[type="submit"]').click();
  await app.getByRole("heading", { name: "Transferts", exact: true }).waitFor();
  serviceUnavailable = true;
  await app.locator("#connection").waitFor({ state: "visible" });
  assert.match(
    await app.locator("#connection").textContent(),
    /Connexion au service interrompue/,
  );
  serviceUnavailable = false;
  await app.locator("#connection").waitFor({ state: "hidden" });
  expired = true;
  await app.getByRole("button", { name: "Paramètres", exact: false }).click();
  await app.locator("#login-screen").waitFor({ state: "visible" });
  assert.equal(await app.locator("#password").isVisible(), false);
  assert.match(
    await app.locator("#login-help").textContent(),
    /Connectez-vous à DSM/,
  );
  assert.deepEqual(errors, []);
  console.log(
    "DSM UI: settings accessible at four window sizes, destination saved, session reuse and desktop CSS isolation passed.",
  );
} finally {
  await browser.close();
}
