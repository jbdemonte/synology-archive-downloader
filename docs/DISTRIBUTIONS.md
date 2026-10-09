# Packaging for DSM distributions

The standalone package is `ArchiveStation`; the proposed SynoCommunity edition is `archivestation`. Each uses its own service account and state directory. These options do not migrate data or grant filesystem permissions.

## Common payload

Run the staging tool from the source archive. It copies application code and assets unchanged, renders the DSM launcher and gateway interpreter, writes the package version and generates notification catalogs. It does not bundle Python or install dependencies.

```sh
python3 scripts/stage_synology.py \
  --destination staging \
  --version 1.0.1-2 \
  --package-id archivestation \
  --python-relative env/bin/python3
```

The interpreter path is relative to `/var/packages/<package-id>/target/`. The standalone builder uses this same tool with `ArchiveStation` and `python/bin/python3`. Supply the actual DSM package version, including the package revision, for cache invalidation and the displayed version.

For an incremental build, run staging again with the final package version before creating the SPK, and regenerate its `INFO` metadata. A cached dependency build can retain an older package revision. Staging can be repeated in the same destination; it recreates the launcher and asset URLs from the original templates and preserves unrelated files such as the dependency wheelhouse.

DSM's outer `ui/style.css` must remain empty of application styles: DSM loads it into the desktop. Application styles belong in `ui/web/`, inside the iframe. The gateway's `@PYTHON@` template is rendered during staging and must not be installed directly.

## Service configuration

Set these environment variables in the package's service script before starting the application:

| Variable | Standalone default | SynoCommunity |
| --- | --- | --- |
| `ARCHIVE_STATION_PACKAGE_ID` | `ArchiveStation` | `archivestation` |
| `ARCHIVE_STATION_SERVICE_USER` | `ArchiveStation` | `sc-archivestation` |
| `ARCHIVE_STATION_PACKAGE_CENTER` | `0` | `1` |

The package ID selects the DSM notification namespace. The service-user value is shown in translated permission guidance; it does not change the process identity or filesystem ownership. DSM must start the process under that account through `conf/privilege`.

With Package Center mode enabled, GitHub checks are disabled server-side, including manual requests. The frontend hides their controls using the distribution information returned by `/api/settings`. These deployment options cannot be changed through saved user settings. Existing update preferences are retained for a possible return to the standalone edition.

The browser derives its same-origin gateway path from the installed DSM URL. Authentication still requires a DSM administrator session; the backend must bind to loopback and start with `--dsm-auth`.

## Validation

Run `make check` and `make test-ui` before building. Optional artifact checks live in this repository rather than in the spksrc recipe:

```sh
ARCHIVE_STATION_SPK=/path/to/archivestation_noarch-dsm7_1.0.1-2.spk \
  PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -p test_spksrc_artifact.py -v
```

Test a fresh installation and an upgrade during a transfer on DSM. Keep a stopped-service backup for manual migration between package identities, and grant the new account access to existing destinations. Never run both editions against the same files simultaneously.

The artifact checks derive the version from `INFO` and compare it with the SPK filename, application version, launcher URL and asset URLs. When changing a recipe revision, also build without cleaning the previous working directory and run these checks to detect stale metadata.
