"use strict";
// Offline catalogs. French message IDs preserve existing UI text and make
// additions easy to find; English is the fallback for other languages.
const ArchiveI18n = (() => {
  const languages = [
    ["en", "English", "🇬🇧"],
    ["fr", "Français", "🇫🇷"],
    ["es", "Español", "🇪🇸"],
    ["pt", "Português", "🇵🇹"],
    ["pt-BR", "Português (Brasil)", "🇧🇷"],
    ["de", "Deutsch", "🇩🇪"],
    ["it", "Italiano", "🇮🇹"],
    ["pl", "Polski", "🇵🇱"],
    ["nl", "Nederlands", "🇳🇱"],
    ["tr", "Türkçe", "🇹🇷"],
    ["id", "Bahasa Indonesia", "🇮🇩"],
    ["cs", "Čeština", "🇨🇿"],
    ["ro", "Română", "🇷🇴"],
    ["hu", "Magyar", "🇭🇺"],
    ["sv", "Svenska", "🇸🇪"],
    ["da", "Dansk", "🇩🇰"],
    ["nb", "Norsk bokmål", "🇳🇴"],
    ["fi", "Suomi", "🇫🇮"],
    ["ja", "日本語", "🇯🇵"],
    ["ko", "한국어", "🇰🇷"],
    ["zh-Hans", "简体中文", "🇨🇳"],
    ["zh-Hant", "繁體中文", "🇹🇼"],
    ["ru", "Русский", "🇷🇺"],
    ["th", "ไทย", "🇹🇭"],
    ["uk", "Українська", "🇺🇦"],
    ["el", "Ελληνικά", "🇬🇷"],
    ["vi", "Tiếng Việt", "🇻🇳"],
  ];
  const dsmCodes = {
    enu: "en",
    fre: "fr",
    ger: "de",
    spn: "es",
    ita: "it",
    ptg: "pt",
    ptb: "pt-BR",
    nld: "nl",
    dan: "da",
    sve: "sv",
    nor: "nb",
    fin: "fi",
    pol: "pl",
    csy: "cs",
    hun: "hu",
    trk: "tr",
    rus: "ru",
    jpn: "ja",
    krn: "ko",
    chs: "zh-Hans",
    cht: "zh-Hant",
    tha: "th",
    rom: "ro",
    ukr: "uk",
    ell: "el",
    vit: "vi",
  };
  const version = new URL(document.currentScript.src).search;
  const cache = new Map();
  let locale = "en",
    dictionary = {},
    english = {},
    revision = 0;
  function match(value) {
    const tag = String(value || "")
      .replaceAll("_", "-")
      .toLowerCase();
    if (dsmCodes[tag]) return dsmCodes[tag];
    const exact = languages.find(([code]) => code.toLowerCase() === tag);
    if (exact) return exact[0];
    if (tag.startsWith("zh"))
      return /hant|tw|hk|mo/.test(tag) ? "zh-Hant" : "zh-Hans";
    if (["no", "nn"].includes(tag.split("-")[0])) return "nb";
    return languages.find(([code]) => code === tag.split("-")[0])?.[0];
  }
  function automatic(serverDefault) {
    let sessionLanguage;
    try {
      sessionLanguage = window.parent.SYNO?.SDS?.Session?.lang;
    } catch {
      /* standalone */
    }
    return (
      [sessionLanguage, serverDefault, ...navigator.languages]
        .map(match)
        .find(Boolean) || "en"
    );
  }
  function t(message, values = {}) {
    const text =
      dictionary[message] ?? english[message] ?? String(message ?? "");
    return text.replace(/\{(\w+)\}/g, (token, name) =>
      String(values[name] ?? token),
    );
  }
  function translateDocument() {
    document.documentElement.lang = locale;
    document.querySelectorAll("[data-i18n]").forEach((node) => {
      node.textContent = t(node.dataset.i18n);
    });
    for (const attribute of ["title", "aria-label", "placeholder"])
      document.querySelectorAll(`[data-i18n-${attribute}]`).forEach((node) => {
        node.setAttribute(
          attribute,
          t(node.getAttribute(`data-i18n-${attribute}`)),
        );
      });
  }
  async function load(code) {
    if (!cache.has(code)) {
      const controller = new AbortController();
      const deadline = setTimeout(() => controller.abort(), 10000);
      const request = fetch(`locales/${code}.json${version}`, {
        signal: controller.signal,
      })
        .then((response) => {
          if (!response.ok)
            throw new Error(`Language catalog unavailable: ${code}`);
          return response.json();
        })
        .catch((error) => {
          cache.delete(code);
          throw error;
        })
        .finally(() => clearTimeout(deadline));
      cache.set(code, request);
    }
    return cache.get(code);
  }
  async function apply(preference = "auto", serverDefault = "") {
    const next =
      preference === "auto"
        ? automatic(serverDefault)
        : match(preference) || "en";
    if (next === locale && Object.keys(dictionary).length) return false;
    const current = ++revision;
    const [base, selected] = await Promise.all([load("en"), load(next)]);
    if (current !== revision) return false;
    english = base;
    dictionary = selected;
    locale = next;
    translateDocument();
    return true;
  }
  return {
    t,
    apply,
    languages,
    match,
    get locale() {
      return locale;
    },
  };
})();
