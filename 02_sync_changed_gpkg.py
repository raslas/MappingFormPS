"""
02_sync_changed_gpkg.py

Reads the list of differing project folders produced by 01_check_cloud_changes.py
(01_check_cloud_changes_differ.txt) and downloads the latest MapovaniePrePS.gpkg
for each one from app.qfield.cloud into its local folder under CLOUD_ROOT.

This is a DOWNLOAD-ONLY operation — it pulls the cloud copy down and overwrites
the local MapovaniePrePS.gpkg. The existing local file is first backed up to
MapovaniePrePS.gpkg.bak so nothing is lost. It does NOT upload anything.

  !! If you have local field edits that were never uploaded, downloading will
     replace them with the cloud version (a .bak backup is kept). Make sure the
     cloud copy is the one you want before confirming.

Requirements:
    pip install qfieldcloud-sdk python-dotenv

Credentials are loaded from .env in the same directory:
    QFIELDCLOUD_USERNAME=...
    QFIELDCLOUD_PASSWORD=...

Usage:
    1. Run 01_check_cloud_changes.py first to refresh the differ list.
    2. python 02_sync_changed_gpkg.py
"""

import os
import shutil
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("'python-dotenv' not installed — run:  pip install python-dotenv")

try:
    from qfieldcloud_sdk.sdk import Client, FileTransferType
except ImportError:
    raise ImportError("'qfieldcloud-sdk' not installed — run:  pip install qfieldcloud-sdk")

# ── Configuration ────────────────────────────────────────────────────────────

load_dotenv(Path(__file__).parent / ".env")

CLOUD_ROOT  = Path(r"C:\Users\RASLAS\QField\cloud")
DIFFER_FILE = Path(__file__).parent / "01_check_cloud_changes_differ.txt"
TARGET_FILE = "MapovaniePrePS.gpkg"
API_URL  = "https://app.qfield.cloud/api/v1/"
USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")


# ── Helpers ──────────────────────────────────────────────────────────────────

def read_differ_folders(path: Path) -> list[str]:
    """Read folder names from the differ file, skipping the '# checked:' header
    and any blank lines."""
    if not path.exists():
        raise FileNotFoundError(
            f"Differ list not found: {path.name}\n"
            "Run 01_check_cloud_changes.py first to generate it."
        )
    names = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.append(line)
    return names


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    if not USERNAME or not PASSWORD:
        raise RuntimeError("QFIELDCLOUD_USERNAME / QFIELDCLOUD_PASSWORD not set in .env")

    folders = read_differ_folders(DIFFER_FILE)
    if not folders:
        print(f"No folders listed in {DIFFER_FILE.name} — nothing to download.")
        return

    print(f"Folders to sync (from {DIFFER_FILE.name}):")
    for name in folders:
        print(f"  - {name}")
    print(f"\nThis will DOWNLOAD '{TARGET_FILE}' from QFieldCloud and overwrite the "
          f"local copy in each folder (a .bak backup is kept).")
    answer = input("Continue? [yes/no]: ").strip().lower()
    if answer not in ("yes", "y", "ok"):
        print("Aborted.")
        return

    print(f"\nLogging in as {USERNAME}...")
    client = Client(url=API_URL)
    client.login(USERNAME, PASSWORD)

    # Build name -> project map. If a name is duplicated, prefer one owned by us.
    projects_by_name: dict[str, dict] = {}
    for proj in client.list_projects():
        existing = projects_by_name.get(proj["name"])
        if existing is None or proj["owner"] == USERNAME:
            projects_by_name[proj["name"]] = proj

    downloaded, skipped = [], []

    for name in folders:
        print(f"\n=== {name} ===")

        project = projects_by_name.get(name)
        if project is None:
            print("  ! No matching project in QFieldCloud — skipped.")
            skipped.append(name)
            continue

        local_folder = CLOUD_ROOT / name
        if not local_folder.is_dir():
            print(f"  ! Local folder not found: {local_folder} — skipped.")
            skipped.append(name)
            continue

        remote = client.list_remote_files(project["id"])
        if not any(f["name"] == TARGET_FILE for f in remote):
            print(f"  ! '{TARGET_FILE}' not present in cloud project — skipped.")
            skipped.append(name)
            continue

        # Back up the existing local file before overwriting.
        local_path = local_folder / TARGET_FILE
        if local_path.exists():
            backup = local_path.with_suffix(local_path.suffix + ".bak")
            shutil.copy2(local_path, backup)
            print(f"  Backed up existing file -> {backup.name}")

        print(f"  Downloading '{TARGET_FILE}'...", end=" ", flush=True)
        client.download_file(
            project["id"],
            FileTransferType.PROJECT,
            local_path,
            Path(TARGET_FILE),
            show_progress=False,
            remote_etag=None,            # always fetch the cloud copy
        )
        size_mb = local_path.stat().st_size / 1_048_576
        print(f"done ({size_mb:.1f} MB)")
        downloaded.append(name)

    # ── Summary ────────────────────────────────────────────────────────────
    print("\n" + "=" * 64)
    print(f"Downloaded {TARGET_FILE} for {len(downloaded)} folder(s):")
    for name in downloaded:
        print(f"  - {name}")
    if skipped:
        print(f"Skipped {len(skipped)} folder(s):")
        for name in skipped:
            print(f"  - {name}")


if __name__ == "__main__":
    main()
