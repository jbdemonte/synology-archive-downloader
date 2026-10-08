"""Plain-text download summaries; report errors never stop download workers."""

import json
import logging
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from .archive import safe_path
from .config import dsm_language

LOG = logging.getLogger(__name__)


def stamp(timestamp, unavailable):
    return (
        datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        if timestamp
        else unavailable
    )


def duration(seconds, french=False):
    days, rest = divmod(max(0, int(seconds)), 86400)
    hours, rest = divmod(rest, 3600)
    minutes, seconds = divmod(rest, 60)
    day_unit = "j" if french else "d"
    return f"{days} {day_unit} {hours:02d} h {minutes:02d} min {seconds:02d} s"


def size(value, french=False):
    units = (
        ("octets", "Kio", "Mio", "Gio", "Tio") if french else ("bytes", "KiB", "MiB", "GiB", "TiB")
    )
    amount, unit = float(value), 0
    while amount >= 1024 and unit < len(units) - 1:
        amount /= 1024
        unit += 1
    exact = f"{value:,}".replace(",", " ")
    return f"{amount:.2f} {units[unit]} ({exact} {units[0]})" if unit else f"{exact} {units[0]}"


def report_header(job_id):
    return f"Archive Station — Internet Archive Downloader\nTask: {job_id}\n"


def render_report(job, settings, now, incidents=()):
    language = settings.get("language", "auto")
    if language == "auto":
        language = settings.get("report_language", "auto")
    french = (dsm_language() if language == "auto" else language) in {"fr", "fre"}

    def tr(en, fr):
        return fr if french else en

    unavailable = tr("Not available", "Non disponible")
    end = job["finished_at"] or (now if job["status"] in {"queued", "running", "paused"} else None)
    status = {
        "queued": tr("Queued", "En attente"),
        "running": tr("Downloading", "En cours"),
        "paused": tr("Paused", "En pause"),
        "completed": tr("Completed", "Terminé"),
        "error": tr("Needs attention", "À vérifier"),
        "cancelled": tr("Cancelled", "Annulé"),
    }.get(job["status"], job["status"])
    lines = [
        report_header(job["id"]).rstrip(),
        "",
        tr("DOWNLOAD REPORT", "RAPPORT DE TÉLÉCHARGEMENT"),
        "=" * 64,
    ]

    def row(label, value):
        lines.append(f"{label:<24} : {' '.join(str(value).split())}")

    def section(title):
        lines.extend(["", title, "-" * 64])

    row(tr("Archive", "Archive"), job["title"])
    row(tr("Identifier", "Identifiant"), job["identifier"])
    row(tr("Status", "État"), status)
    row(
        tr("Source URL", "URL source"),
        job["source_url"] or f"https://archive.org/details/{job['identifier']}",
    )
    row(
        tr("Download URL", "URL de téléchargement"),
        f"https://archive.org/download/{job['identifier']}",
    )
    row(tr("Destination", "Destination"), str(Path(job["destination"]) / job["identifier"]))
    section(tr("DATES AND DURATION", "DATES ET DURÉE"))
    row(tr("Task created", "Tâche créée le"), stamp(job["created"], unavailable))
    row(tr("Report updated", "Rapport mis à jour le"), stamp(now, unavailable))
    row(tr("Task finished", "Tâche terminée le"), stamp(job["finished_at"], unavailable))
    row(
        tr("Elapsed duration", "Durée écoulée"),
        duration(end - job["created"], french) if end else unavailable,
    )
    lines.append(
        tr(
            "Duration includes pauses and NAS downtime.",
            "La durée inclut les pauses et les périodes d’arrêt du NAS.",
        )
    )
    section(tr("DOWNLOAD RESULTS", "BILAN DU TÉLÉCHARGEMENT"))
    row(
        tr("Files completed", "Fichiers terminés"),
        f"{job['completed_files']} / {job['file_count']}",
    )
    row(tr("Files with errors", "Fichiers en erreur"), job["failed_files"])
    row(tr("Data downloaded", "Volume téléchargé"), size(job["downloaded"], french))
    row(tr("Completed file data", "Volume terminé"), size(job["completed_bytes"], french))
    row(tr("Total known size", "Taille totale connue"), size(job["total_size"], french))
    row(tr("Files of unknown size", "Taille inconnue"), job["unknown_sizes"])
    lines.append(
        tr(
            "Downloaded data includes retained partial files, not repeated network transfers.",
            "Le volume téléchargé inclut les fichiers partiels conservés, "
            "sans compter les retransferts.",
        )
    )
    section(tr("ERROR HISTORY", "HISTORIQUE DES ERREURS"))
    row(tr("Incidents recorded", "Incidents consignés"), job.get("incident_count", 0))
    row(tr("Still unresolved", "Encore non résolus"), job.get("unresolved_incidents", 0))
    since = job.get("error_history_since")
    row(tr("History recorded since", "Historique conservé depuis"), stamp(since, unavailable))
    if since and since > job["created"] + 1:
        lines.append(
            tr(
                "Earlier resolved errors were not retained by previous versions.",
                "Les erreurs antérieures déjà corrigées n’étaient pas conservées "
                "par les versions précédentes.",
            )
        )
    if not incidents:
        lines.append(tr("No incidents recorded.", "Aucun incident consigné."))
    else:
        lines.append(
            tr(
                "Identical errors are grouped per file; successful transfers resolve them.",
                "Les erreurs identiques sont regroupées par fichier ; "
                "un transfert réussi les marque comme résolues.",
            )
        )
    for incident in incidents:
        lines.append("")
        row(
            tr("Outcome", "Résultat"),
            tr("Resolved", "Résolu") if incident["resolved_at"] else tr("Unresolved", "Non résolu"),
        )
        row(tr("File", "Fichier"), incident["name"] or tr("Whole task", "Tâche entière"))
        row(tr("First error", "Première erreur"), stamp(incident["first_at"], unavailable))
        row(tr("Latest error", "Dernière erreur"), stamp(incident["last_at"], unavailable))
        row(tr("Occurrences", "Occurrences"), incident["occurrences"])
        if incident["attempt"]:
            row(tr("Last attempt number", "Dernière tentative n°"), incident["attempt"])
        row(tr("Message", "Message"), incident["message"])
        if incident["resolved_at"]:
            row(tr("Resolved at", "Résolu le"), stamp(incident["resolved_at"], unavailable))
    section(tr("SELECTION AND VERIFICATION", "SÉLECTION ET VÉRIFICATION"))
    row(
        tr("Source file mode", "Mode des fichiers source"),
        tr("Original files only", "Fichiers originaux uniquement")
        if job["mode"] == "original"
        else tr("All public files", "Tous les fichiers publics"),
    )
    row(tr("Filename filter", "Filtre de noms"), job["pattern"] or tr("None", "Aucun"))
    row(
        tr("Checksum setting", "Vérification SHA-1 / MD5"),
        tr("Enabled", "Activée") if settings["verify_checksums"] else tr("Disabled", "Désactivée"),
    )
    lines.append(
        tr(
            "This is the current verification setting; checksums are checked when available.",
            "Il s’agit du réglage actuel ; les sommes de contrôle sont vérifiées si disponibles.",
        )
    )
    lines.extend(
        [
            "",
            tr(
                "Automatically generated by Archive Station. Updated while the task progresses.",
                "Généré automatiquement par Archive Station et actualisé pendant la tâche.",
            ),
            "",
        ]
    )
    return "\n".join(lines)


class Reports:
    def __init__(self, store, settings):
        self.store, self.settings = store, settings
        self.stop = threading.Event()
        self.thread = None
        self.written = {}

    def start(self):
        self.thread = threading.Thread(target=self.run, name="download-reports", daemon=True)
        self.thread.start()

    def run(self):
        while not self.stop.is_set():
            try:
                self.update()
            except Exception:
                LOG.exception("Report update failed; retrying on the next interval")
            self.stop.wait(15)

    def shutdown(self, timeout=10):
        self.stop.set()
        previous = self.thread

        def finish():
            # Serialize the final snapshot with a possible in-flight write. Bound
            # the caller's wait, including filesystem I/O in the final update.
            if previous:
                previous.join()
            try:
                self.update()
            except Exception:
                LOG.exception("Final report update failed")

        self.thread = threading.Thread(target=finish, name="final-reports", daemon=True)
        self.thread.start()
        self.thread.join(timeout=timeout)
        return not self.thread.is_alive()

    def update(self):
        for job in self.store.jobs():
            signature = (
                job["status"],
                job["downloaded"],
                job["completed_files"],
                job["failed_files"],
                job["file_count"],
                job["total_size"],
                job.get("manifest_revision", 0),
                job.get("incident_count", 0),
                job.get("unresolved_incidents", 0),
                self.settings.get().get("language", "auto"),
                self.settings.get().get("report_language", "auto"),
                self.settings.get()["verify_checksums"],
                job["finished_at"],
                job["unknown_sizes"],
            )
            if self.written.get(job["id"]) == signature:
                continue
            try:
                self.write(job)
                self.written[job["id"]] = signature
            except (OSError, ValueError):
                LOG.warning("Could not update report for %s", job["identifier"], exc_info=True)

    def write(self, job):
        root = self.settings.directory(job["destination"])
        # Per-job UUID keeps reports separate from archived files and earlier tasks.
        name = f"ArchiveStation-report-{job['id']}.txt"
        path = safe_path(root, f"{job['identifier']}/{name}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            with path.open(encoding="utf-8") as existing:
                prefix = existing.read(len(report_header(job["id"])))
            if prefix != report_header(job["id"]):
                raise ValueError("An unrelated file already uses the report name")
        value = render_report(
            job, self.settings.get(), time.time(), self.store.error_history(job["id"])
        )
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=path.parent,
                prefix=".archive-station-report-",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                output.write(value)
                output.flush()
                os.fsync(output.fileno())
            temporary.chmod(0o644)
            os.replace(temporary, path)
            self.remove_legacy_report(root, job)
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)

    @staticmethod
    def remove_legacy_report(root, job):
        """Remove only our old JSON report, after the text replacement is durable."""
        try:
            legacy = safe_path(root, f"{job['identifier']}/ArchiveStation-report-{job['id']}.json")
            if legacy.exists() and legacy.stat().st_size < 65536:
                data = json.loads(legacy.read_text(encoding="utf-8"))
                if (
                    isinstance(data, dict)
                    and data.get("application") == "Archive Station"
                    and data.get("report_version") == 1
                    and data.get("job_id") == job["id"]
                ):
                    legacy.unlink()
        except (OSError, ValueError):
            # An unrelated, unreadable or linked file is never removed.
            pass
