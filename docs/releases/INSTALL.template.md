# Install Archive Station @VERSION@

This package targets **Synology DSM 7 on x86_64**. It has been tested on a DS918+
running DSM 7.1.1. It includes Python and does not require Docker.

1. Download `ArchiveStation-@VERSION@-x86_64.spk` and its `.spk.sha256` file.
2. Optionally verify the download from their directory:

   ```sh
   shasum -a 256 -c ArchiveStation-@VERSION@-x86_64.spk.sha256
   ```

   On Linux, use `sha256sum -c` instead of `shasum -a 256 -c`.

3. Sign in to DSM as an administrator and open **Package Center → Manual Install**.
4. Select the `.spk`, review the package information and follow the installation steps.
5. Open **Archive Station** from the DSM menu, then choose **Settings**.

The package initially uses the `ArchiveStation` shared folder. To use another share,
grant **Read/Write** to the **ArchiveStation** system internal user in
**Control Panel → Shared Folder → Edit → Permissions**, then select it in the app.

## Upgrading an existing installation

Install the newer `.spk` through Manual Install over the existing package. The package
briefly restarts; task state, settings, completed files and resumable partial files are
preserved. Running tasks resume and manually paused tasks stay paused. Check progress
after the upgrade. Do not delete `.archive-station-parts` if you want partial-file resumption.

## Troubleshooting

- Close and reopen Archive Station after an upgrade to load its new interface.
- Orange folders need permissions; blue folders allow reading only.
- The default destination is locked while tasks are running or queued. Pause and wait
  for active workers to stop before changing it. Existing tasks keep their original paths.
- A checksum confirms that the file matches the published digest; it is not a publisher signature.

Documentation: https://github.com/jbdemonte/synology-archive-downloader

This independent community package is not published by Synology or Internet Archive.
