# Translating Archive Station

The interface ships with 27 offline catalogs in `src/archive_station/static/locales/`. No translation service is contacted by the application, during installation, or during a normal build.

## Language selection

The saved setting is either a supported language code or `auto`. Automatic selection first checks `SYNO.SDS.Session.lang` in the same-origin DSM desktop, then the server’s DSM language, then the browser’s preferred languages. Unsupported languages fall back to English. A manual choice in Settings persists on the NAS and takes priority. Flags are visual hints; each option also includes the language’s native name.

Settings sorts native names alphabetically using the current interface locale, ignoring case and accents. The automatic option always stays first; flags do not affect the order.

DSM abbreviations such as `fre`, `enu`, `ger`, `chs` and `cht` are mapped to locale codes. Regional browser tags such as `fr-CA`, `pt-BR` and `zh-TW` are normalized. The interface formats numbers and times with the selected locale.

## Editing a translation

1. Edit the relevant JSON catalog. French strings are stable message IDs inherited from the initial interface; `en.json` provides the English reference.
2. Preserve interpolation tokens such as `{count}`, `{current}`, `{total}` and `{time}` exactly. Translate the surrounding text; never translate those token names.
3. Keep Archive Station, ArchiveStation (the DSM system user), Archive.org, SHA-1 and MD5 unchanged.
4. Use plain text. Strings are inserted as text, and dynamic HTML contexts escape translated values.
5. Run `make check` and `make test-ui`. The checks require matching keys in all catalogs and validate interpolation tokens. Browser tests save and display every supported language.

Use `data-i18n` for static text, and `data-i18n-title`, `data-i18n-aria-label` or `data-i18n-placeholder` for attributes. Dynamic text uses `t(message, values)`. When adding a message, update every catalog; use a clearly documented English fallback during development rather than silently omitting a key.

The initial expanded catalogs were machine-assisted, with English and important action labels reviewed. Native-speaker corrections are welcome, especially for longer help text. Low-level operating-system or network diagnostic details may remain in the language supplied by the underlying service.

## Adding a language

Add its code, native name and flag to `static/i18n.js`, include it in `config.LANGUAGES`, and create a complete JSON catalog. Add any DSM language-code mapping. Check the language selector, analysis progress, permission notices and settings at narrow window sizes. Update the supported-language count in the browser test and README.
