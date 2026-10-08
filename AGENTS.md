# Repository Guidelines

## Project Structure & Module Organization

This directory (`syno-archive-org-dl`) is currently an empty project scaffold. No source code, tests, assets, dependency manifests, or Git metadata are present. Update this guide as the implementation takes shape.

When adding the initial implementation, keep responsibilities separate: application code in `src/`, automated tests in `tests/`, developer utilities in `scripts/`, and supporting documentation in `docs/`. Create these directories only when needed. Document the entry point and architecture in `README.md`.

## Build, Test, and Development Commands

No build, test, or local run commands are configured yet. Do not assume commands such as `npm test` or `make build` are available.

When selecting the toolchain, document exact installation, execution, testing, and formatting commands in `README.md`. Prefer reproducible dependency installation and commit the appropriate lockfile when the ecosystem supports it.

## Coding Style & Naming Conventions

Use the chosen language's standard formatter and linter, and commit their configuration with the initial code. Follow their indentation and naming rules consistently. Use descriptive module and function names that reflect one responsibility. Avoid introducing competing formatting conventions.

## Testing Guidelines

No testing framework or coverage threshold is established. Add a suitable framework alongside the first implementation. Keep test names descriptive of expected behavior and place fixtures under `tests/fixtures/` if needed.

If download functionality is introduced, cover failure responses, interrupted transfers, and existing destination files. Use temporary directories and mocked network responses so routine tests remain deterministic and do not require external services.

## Commit & Pull Request Guidelines

No Git history is available to establish an existing commit convention. Use concise, imperative subjects, such as `Add download configuration validation`, and keep commits focused.

Pull requests should describe the change, its purpose, and validation performed. Link relevant issues and call out configuration changes. Include screenshots only for visual changes.

## Security & Configuration

Keep credentials, local configuration, downloaded content, and generated output out of version control. Provide sanitized configuration examples and document required settings before adding integrations.
