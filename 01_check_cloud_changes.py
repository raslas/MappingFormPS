"""
check_cloud_changes.py

Reports which local QField project folders have a modified MapovaniePrePS.gpkg
file in their QFieldCloud counterpart. READ-ONLY: it never uploads, downloads,
or syncs anything — it only prints a comparison report.

Only folders under CLOUD_ROOT whose name starts with "SKUEV" (capitals) are
checked. Each folder name is treated as the QFieldCloud project name.

For every file it compares the local MD5 against the cloud md5sum and classifies
it internally as:
    =  in sync       (same MD5 on both sides)
    ~  modified       (present on both sides, different MD5)
    +  local only     (exists locally, not in cloud  -> would be uploaded)
    -  cloud only     (exists in cloud, not locally  -> would be downloaded)

Requirements:
    pip install qfieldcloud-sdk python-dotenv

Credentials are loaded from .env in the same directory as this script:
    QFIELDCLOUD_USERNAME=...
    QFIELDCLOUD_PASSWORD=...

Usage:
    python 01_check_cloud_changes.py
"""

import hashlib
import os
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("'python-dotenv' not installed — run:  pip install python-dotenv")

try:
    from qfieldcloud_sdk.sdk import Client
except ImportError:
    raise ImportError("'qfieldcloud-sdk' not installed — run:  pip install qfieldcloud-sdk")

# ── Configuration ────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent / ".env")

CLOUD_ROOT = Path(r"C:\Users\RASLAS\QField\cloud")
FOLDER_PREFIX = "SKUEV"                       # only folders starting with this (case-sensitive)
TARGET_FILE = "MapovaniePrePS.gpkg"           # only report folders where this file is modified
API_URL  = "https://app.qfield.cloud/api/v1/"
USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")

# Sidecar / transient files that exist locally but are never tracked by the cloud.
IGNORE_SUFFIXES = (".gpkg-wal", ".gpkg-shm", "~")


# ── Helpers ──────────────────────────────────────────────────────────────────

def md5_of(path: Path) -> str:
    """Return the hex MD5 digest of a file, read in chunks."""
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def local_files(folder: Path) -> dict[str, Path]:
    """
    Map relative-name -> absolute Path for every local file in `folder`,
    skipping dot-folders/dot-files, tilde backups, and transient sidecars —
    matching what QFieldCloud would actually track.
    """
    result: dict[str, Path] = {}
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(folder)
        if any(part.startswith(".") for part in rel.parts):
            continue
        if path.name.endswith(IGNORE_SUFFIXES):
            continue
        result[rel.as_posix()] = path
    return result


def compare(folder: Path, remote: list[dict]) -> dict[str, list[str]]:
    """Compare a local folder against the cloud file list. Returns categorised names."""
    locals_ = local_files(folder)
    remotes = {f["name"].replace("\\", "/"): f for f in remote}

    in_sync, modified, local_only, cloud_only = [], [], [], []

    for name, path in sorted(locals_.items()):
        rf = remotes.get(name)
        if rf is None:
            local_only.append(name)
        elif md5_of(path) == rf["md5sum"]:
            in_sync.append(name)
        else:
            modified.append(name)

    for name in sorted(remotes):
        if name not in locals_:
            cloud_only.append(name)

    return {
        "in_sync": in_sync,
        "modified": modified,
        "local_only": local_only,
        "cloud_only": cloud_only,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not USERNAME or not PASSWORD:
        raise RuntimeError("QFIELDCLOUD_USERNAME / QFIELDCLOUD_PASSWORD not set in .env")

    if not CLOUD_ROOT.exists():
        raise FileNotFoundError(f"Cloud root not found: {CLOUD_ROOT}")

    folders = sorted(
        p for p in CLOUD_ROOT.iterdir()
        if p.is_dir() and p.name.startswith(FOLDER_PREFIX)
    )
    if not folders:
        print(f"No folders starting with '{FOLDER_PREFIX}' under {CLOUD_ROOT}")
        return

    print(f"Logging in as {USERNAME}...")
    client = Client(url=API_URL)
    client.login(USERNAME, PASSWORD)

    # Build name -> project map. If a name is duplicated, prefer one owned by us.
    projects_by_name: dict[str, dict] = {}
    for proj in client.list_projects():
        existing = projects_by_name.get(proj["name"])
        if existing is None or proj["owner"] == USERNAME:
            projects_by_name[proj["name"]] = proj

    print(f"Checking {len(folders)} folder(s) against QFieldCloud "
          f"(prefix '{FOLDER_PREFIX}'). READ-ONLY report - nothing is synced.")
    print(f"Reporting only folders where {TARGET_FILE} is modified.\n")

    target_modified_projects: list[str] = []

    for folder in folders:
        name = folder.name
        print(f"=== {name} " + "=" * max(0, 60 - len(name)))

        project = projects_by_name.get(name)
        if project is None:
            print("  No matching project in QFieldCloud; skipped.\n")
            continue

        remote = client.list_remote_files(project["id"])
        result = compare(folder, remote)

        if TARGET_FILE in result["modified"]:
            print(f"  ~ modified    {TARGET_FILE}")
            target_modified_projects.append(name)
        else:
            print(f"  {TARGET_FILE} is not modified.")

        last = project.get("data_last_updated_at") or "?"
        print(f"  cloud last updated: {last}\n")

    # ── Summary ────────────────────────────────────────────────────────────
    print("=" * 64)
    if target_modified_projects:
        print(f"{len(target_modified_projects)} of {len(folders)} folder(s) have modified {TARGET_FILE}:")
        for name in target_modified_projects:
            print(f"  - {name}")
    else:
        print(f"No modified {TARGET_FILE} found in {len(folders)} checked folder(s).")

    # Write the check timestamp, then the names of folders with a modified
    # target file.
    # (one per line) to a sidecar file.
    out_path = Path(__file__).parent / "01_check_cloud_changes_differ.txt"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out_path.write_text(
        f"# checked: {timestamp}\n"
        + "".join(f"{name}\n" for name in target_modified_projects),
        encoding="utf-8",
    )
    print(f"\nFolder names with modified {TARGET_FILE} written to: {out_path.name} "
          f"({len(target_modified_projects)} folder(s))")


if __name__ == "__main__":
    main()
