"""Immutable packaging options, supplied by the service rather than saved settings."""

import os
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Distribution:
    package_id: str = "ArchiveStation"
    service_user: str = "ArchiveStation"
    update_checks: bool = True

    def __post_init__(self):
        for value in (self.package_id, self.service_user):
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_-]{0,63}", value):
                raise ValueError("Invalid package identity")

    @classmethod
    def from_env(cls):
        mode = os.environ.get("ARCHIVE_STATION_PACKAGE_CENTER", "0")
        if mode not in {"0", "1"}:
            raise ValueError("ARCHIVE_STATION_PACKAGE_CENTER must be 0 or 1")
        return cls(
            package_id=os.environ.get("ARCHIVE_STATION_PACKAGE_ID", "ArchiveStation"),
            service_user=os.environ.get("ARCHIVE_STATION_SERVICE_USER", "ArchiveStation"),
            update_checks=mode != "1",
        )
