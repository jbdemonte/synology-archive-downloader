import assert from "node:assert/strict";
import { chromium } from "playwright";

// Exercise the real client with a simulated DSM desktop and HttpOnly cookie.
// No NAS session or password is read; the native verifier is tested in Python.
const base = process.env.TEST_URL || "http://127.0.0.1:8275";
const browser = await chromium.launch({
  headless: true,
  executablePath: process.env.CHROMIUM_PATH || undefined,
});
const errors = [];
async function scenario(packageId, options, check) {
  const context = await browser.newContext({ locale: "en-US" });
  await context.addCookies([
    { name: "id", value: "fixture-session", url: base, httpOnly: true },
  ]);
  const page = await context.newPage();
  page.setDefaultTimeout(10000);
  page.on("pageerror", (error) => errors.push(error.message));
  const dsmBase = `/webman/3rdparty/${packageId}/`;
  const state = {
    desktopToken: "",
    acceptedToken: "fresh-token",
    session: { success: true, SynoToken: "fresh-token" },
    tokenCalls: 0,
    authCalls: 0,
    mutationCalls: 0,
    mutations: 0,
    redirectCalls: 0,
    ...options,
  };
  await page.route(`${base}/unexpected`, (route) => {
    state.redirectCalls++;
    return route.fulfill({ json: state.session });
  });
  await page.route(`${base}/desktop-test`, (route) =>
    route.fulfill({
      contentType: "text/html",
      body: `<script>window.SYNO={SDS:{Session:{SynoToken:${JSON.stringify(state.desktopToken)},lang:"enu"}}}</script>
        <iframe name="archive" src="${dsmBase}web/index.html" style="width:1200px;height:850px"></iframe>`,
    }),
  );
  await page.route(`${base}/webman/login.cgi`, async (route) => {
    state.tokenCalls++;
    assert.equal(route.request().method(), "GET");
    assert.match(route.request().headers().cookie, /id=fixture-session/);
    assert.equal(route.request().postData(), null);
    await new Promise((resolve) => setTimeout(resolve, 100));
    if (state.redirect)
      return route.fulfill({
        status: 302,
        headers: { Location: "/unexpected" },
      });
    if (state.html)
      return route.fulfill({
        contentType: "text/html",
        body: "<html>DSM</html>",
      });
    return route.fulfill({ json: state.session });
  });
  await page.route(`${base}${dsmBase}**`, async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    if (!url.pathname.endsWith("gateway.cgi")) {
      const asset = url.pathname.split("/web/")[1];
      const response = await route.fetch({
        url: base + "/" + (asset === "index.html" ? "" : asset),
      });
      return route.fulfill({ response });
    }
    const path = url.searchParams.get("route");
    assert.ok(path.startsWith("/api/"));
    assert.ok(!["/api/login", "/api/password", "/api/logout"].includes(path));
    assert.equal(url.searchParams.has("SynoToken"), false);
    if (path === "/api/auth") state.authCalls++;
    if (path === "/api/test-mutation") state.mutationCalls++;
    if (state.gatewayError) return route.fulfill({ json: state.gatewayError });
    if (
      state.reject ||
      request.headers()["x-syno-token"] !== state.acceptedToken
    )
      return route.fulfill({
        status: 401,
        json: {
          mode: "dsm",
          error:
            "Session DSM expirée. Reconnectez-vous à DSM, puis rouvrez Archive Station.",
        },
      });
    if (path === "/api/auth")
      return route.fulfill({ json: { mode: "dsm", authenticated: true } });
    if (path.startsWith("/api/test-")) {
      if (path === "/api/test-mutation") state.mutations++;
      return route.fulfill({ json: { ok: true } });
    }
    return route.fulfill({ response: await route.fetch({ url: base + path }) });
  });
  try {
    await page.goto(base + "/desktop-test");
    const app = page.frame({ name: "archive" });
    // Isolate the explicit requests below from background polling.
    await app.evaluate(() => {
      Object.defineProperty(document, "hidden", { get: () => true });
    });
    await check(page, app, state);
    assert.equal(state.redirectCalls, 0);
    assert.equal(await app.locator("#password").isVisible(), false);
    assert.equal(
      await app.evaluate(() =>
        Object.values(localStorage).some((value) => value.includes("token")),
      ),
      false,
    );
  } catch (error) {
    const app = page.frame({ name: "archive" });
    console.error({
      packageId,
      tokenCalls: state.tokenCalls,
      authCalls: state.authCalls,
      errors,
      messages: await app.evaluate(() => ({
        startup: document.querySelector("#startup-error")?.textContent,
        login: document.querySelector("#login-error")?.textContent,
      })),
    });
    throw error;
  } finally {
    await context.close();
  }
}
try {
  for (const packageId of ["ArchiveStation", "archivestation"]) {
    // Missing and stale desktop tokens both recover using the existing cookie.
    for (const desktopToken of ["", "stale-token"])
      await scenario(packageId, { desktopToken }, async (_, app, state) => {
        await app.locator("#application").waitFor({ state: "visible" });
        assert.equal(state.tokenCalls, 1);
        assert.equal(state.authCalls, 2);
        assert.equal(await app.locator("#login-screen").isVisible(), false);
      });
    await scenario(
      packageId,
      { desktopToken: "fresh-token" },
      async (page, app, state) => {
        await app.locator("#application").waitFor({ state: "visible" });
        assert.equal(state.tokenCalls, 0);
        state.acceptedToken = "refreshed-token";
        state.session.SynoToken = state.acceptedToken;
        const results = await app.evaluate(() =>
          Promise.all([api("/api/test-read"), api("/api/test-mutation", {})]),
        );
        assert.deepEqual(results, [{ ok: true }, { ok: true }]);
        assert.equal(state.tokenCalls, 1);
        assert.equal(state.mutationCalls, 2);
        assert.equal(state.mutations, 1);
        assert.equal(await app.locator("#login-screen").isVisible(), false);
        // A later desktop login can update the token without a refresh call.
        state.acceptedToken = "new-desktop-token";
        await page.evaluate(() => {
          window.SYNO.SDS.Session.SynoToken = "new-desktop-token";
        });
        assert.deepEqual(await app.evaluate(() => api("/api/test-read")), {
          ok: true,
        });
        assert.equal(state.tokenCalls, 1);
      },
    );
  }
  for (const options of [
    { session: { success: false } },
    { session: { success: true, SynoToken: "bad\nheader" } },
    { session: { success: true, SynoToken: { value: "not-a-token" } } },
    {
      session: { success: true, SynoToken: "stale-token" },
      desktopToken: "stale-token",
    },
    { html: true },
    { redirect: true },
    { reject: true },
  ])
    await scenario("ArchiveStation", options, async (_, app, state) => {
      await app.locator("#dsm-retry").waitFor({ state: "visible" });
      assert.equal(state.tokenCalls, 1);
      assert.equal(state.authCalls, state.reject ? 2 : 1);
      assert.equal(state.mutations, 0);
    });
  for (const status of [403, 503])
    await scenario(
      "ArchiveStation",
      {
        gatewayError: { _http_status: status, error: "DSM check unavailable" },
      },
      async (_, app, state) => {
        await app.locator("#startup-error").waitFor({ state: "visible" });
        assert.equal(state.tokenCalls, 0);
        assert.equal(state.authCalls, 1);
        assert.equal(await app.locator("#login-screen").isVisible(), false);
      },
    );
  await scenario(
    "ArchiveStation",
    { desktopToken: "fresh-token" },
    async (page, app, state) => {
      await app.locator("#application").waitFor({ state: "visible" });
      await page.clock.install();
      state.reject = true;
      await app.evaluate(() => {
        const originalFetch = window.fetch;
        window.tokenRequests = 0;
        window.fetch = async (url, options) => {
          if (url !== "/webman/login.cgi") return originalFetch(url, options);
          window.tokenRequests++;
          // Headers arrive but the body stalls: the token deadline covers both.
          return {
            ok: true,
            json: () =>
              new Promise((_, reject) => {
                options.signal.addEventListener(
                  "abort",
                  () => {
                    window.tokenTimedOut = true;
                    reject(options.signal.reason);
                  },
                  { once: true },
                );
              }),
          };
        };
        window.cancelRequest = new AbortController();
        api("/api/test-read", undefined, window.cancelRequest.signal).catch(
          (error) => {
            window.cancelled = error.name === "AbortError";
          },
        );
        api("/api/test-other").catch((error) => {
          window.otherStatus = error.status;
        });
      });
      await app.waitForFunction(() => window.tokenRequests === 1);
      await app.evaluate(() => window.cancelRequest.abort());
      await app.waitForFunction(() => window.cancelled === true);
      assert.equal(await app.evaluate(() => window.tokenTimedOut), undefined);
      await page.clock.runFor(5001);
      await app.waitForFunction(() => window.otherStatus === 401);
      assert.equal(await app.evaluate(() => window.tokenTimedOut), true);
      assert.equal(await app.evaluate(() => window.tokenRequests), 1);
    },
  );
  assert.deepEqual(errors, []);
  console.log(
    "DSM authentication UI: cookie session recovery, stale tokens, shared refresh, single mutation, bounded retries and denied sessions passed for both packages.",
  );
} finally {
  await browser.close();
}
