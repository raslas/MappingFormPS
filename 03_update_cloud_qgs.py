"""
03_update_cloud_qgs.py

Uploads the local MapovaciFormularPS.qgs file into existing QFieldCloud
projects listed in SKUEVs_to_UPDATE.txt.

This script updates only the .qgs project file. It does not upload
MapovaniePrePS.gpkg, upload lookup GeoPackages, delete projects, create
projects, or touch local QField cloud folders.

Requirements:
    pip install requests python-dotenv

Credentials are loaded from .env in the same directory:
    QFIELDCLOUD_USERNAME=...
    QFIELDCLOUD_PASSWORD=...

Optional:
    QFIELDCLOUD_TOKEN=...

Usage:
    python 03_update_cloud_qgs.py --dry-run
    python 03_update_cloud_qgs.py
"""

import argparse
import os
from pathlib import Path

try:
    import requests
except ImportError:
    raise ImportError("'requests' not installed - run:  pip install requests python-dotenv")

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("'python-dotenv' not installed - run:  pip install requests python-dotenv")


# -- Configuration -------------------------------------------------------------

PROJECT_DIR = Path(__file__).parent
UPDATE_LIST = PROJECT_DIR / "03_SKUEVs_to_UPDATE.txt"
TARGET_FILE = PROJECT_DIR / "MapovaciFormularPS.qgs"

BASE_URL = "https://app.qfield.cloud/api/v1"

load_dotenv(PROJECT_DIR / ".env")

USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")
TOKEN = os.environ.get("QFIELDCLOUD_TOKEN", "")
OWNER = os.environ.get("QFIELDCLOUD_OWNER", "")


# -- Helpers ------------------------------------------------------------------

def read_project_names(path: Path) -> list[str]:
    """Read project names, skipping blank lines and comments."""
    if not path.exists():
        raise FileNotFoundError(f"Update list not found: {path}")

    names: list[str] = []
    seen: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        if name in seen:
            print(f"  ! duplicate in {path.name}: {name} - keeping first occurrence")
            continue
        seen.add(name)
        names.append(name)
    return names


def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"token {token}"}


def login() -> tuple[str, str]:
    """Return (token, username). Uses QFIELDCLOUD_TOKEN if available."""
    if TOKEN:
        if not USERNAME:
            raise RuntimeError("QFIELDCLOUD_USERNAME must be set when QFIELDCLOUD_TOKEN is used")
        return TOKEN, USERNAME

    if not USERNAME or not PASSWORD:
        raise RuntimeError("QFIELDCLOUD_USERNAME / QFIELDCLOUD_PASSWORD not set in .env")

    resp = requests.post(
        f"{BASE_URL}/auth/login/",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed [{resp.status_code}]: {resp.text}")

    data = resp.json()
    return data["token"], data["username"]


def api_get(token: str, path: str) -> dict | list:
    resp = requests.get(f"{BASE_URL}{path}", headers=auth_headers(token), timeout=60)
    resp.raise_for_status()
    return resp.json()


def upload_file(token: str, project_id: str, local_path: Path) -> None:
    url = f"{BASE_URL}/files/{project_id}/{local_path.name}/"
    with local_path.open("rb") as fh:
        resp = requests.post(
            url,
            headers=auth_headers(token),
            files={"file": (local_path.name, fh, "application/octet-stream")},
            timeout=300,
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed [{resp.status_code}]: {resp.text}")


def build_project_lookup(projects: list[dict], preferred_owner: str) -> dict[str, dict]:
    """Build name -> project lookup, preferring projects owned by preferred_owner."""
    lookup: dict[str, dict] = {}
    for project in projects:
        name = project.get("name", "")
        owner = project.get("owner", "")
        existing = lookup.get(name)
        if existing is None:
            lookup[name] = project
            continue
        if owner == preferred_owner and existing.get("owner") != preferred_owner:
            lookup[name] = project
    return lookup


def confirm(names: list[str], target_file: Path) -> bool:
    size_kb = target_file.stat().st_size / 1024
    print(f"\nThis will UPLOAD '{target_file.name}' ({size_kb:.1f} KB) to {len(names)} project(s):")
    for name in names:
        print(f"  - {name}")
    answer = input("\nContinue? [yes/no]: ").strip().lower()
    return answer in ("yes", "y", "ok")


# -- Main ---------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Upload MapovaciFormularPS.qgs to QFieldCloud projects listed in SKUEVs_to_UPDATE.txt."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Resolve projects and show what would be uploaded, but do not upload anything.",
    )
    args = parser.parse_args()

    if not TARGET_FILE.exists():
        raise FileNotFoundError(f"Target file not found: {TARGET_FILE}")

    names = read_project_names(UPDATE_LIST)
    if not names:
        print(f"No project names found in {UPDATE_LIST.name}.")
        return

    mode = "DRY RUN" if args.dry_run else "UPLOAD"
    print(f"Mode: {mode}")
    print(f"Target file: {TARGET_FILE}")
    print(f"Project list: {UPDATE_LIST.name} ({len(names)} project(s))")

    print(f"\nLogging in as {USERNAME or '(token user)'}...")
    token, username = login()
    preferred_owner = OWNER or username
    print(f"  OK (preferred owner: {preferred_owner})")

    print("\nFetching QFieldCloud projects...")
    projects = api_get(token, "/projects/")
    projects_by_name = build_project_lookup(projects, preferred_owner)

    resolved: list[tuple[str, dict]] = []
    missing: list[str] = []
    for name in names:
        project = projects_by_name.get(name)
        if project is None:
            missing.append(name)
        else:
            resolved.append((name, project))

    print("\nProject resolution:")
    for name, project in resolved:
        print(f"  OK {name} -> {project.get('id')} (owner: {project.get('owner')})")
    for name in missing:
        print(f"  !  {name} -> not found")

    if args.dry_run:
        print(f"\nDry run complete. Would upload {TARGET_FILE.name} to {len(resolved)} project(s).")
        if missing:
            print(f"Missing projects: {len(missing)}")
        return

    if not resolved:
        print("\nNo matching projects found - nothing to upload.")
        return

    if not confirm([name for name, _ in resolved], TARGET_FILE):
        print("Aborted.")
        return

    uploaded: list[str] = []
    failed: list[str] = []

    print("\nUploading...")
    for name, project in resolved:
        print(f"  {name}...", end=" ", flush=True)
        try:
            upload_file(token, project["id"], TARGET_FILE)
        except Exception as exc:
            print(f"FAILED: {exc}")
            failed.append(name)
            continue
        print("done")
        uploaded.append(name)

    print("\n" + "=" * 64)
    print(f"Uploaded {TARGET_FILE.name} to {len(uploaded)} project(s).")
    if missing:
        print(f"Skipped missing project(s): {', '.join(missing)}")
    if failed:
        print(f"Failed upload(s): {', '.join(failed)}")


if __name__ == "__main__":
    main()
