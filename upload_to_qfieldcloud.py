"""
upload_to_qfieldcloud.py

Uploads MapovaciFormularPS project files to app.qfield.cloud.
Standalone Python script — no QGIS installation required.

Requirements:
    pip install requests python-dotenv

Credentials are loaded from .env in the same directory as this script.

Usage:
    1. Fill in your credentials in .env
    2. python upload_to_qfieldcloud.py
"""
# ── Project name — change this to match your QFieldCloud project ───────────────

CLOUD_PROJECT_NAME = "SKUEV0184"

import os
from pathlib import Path

try:
    import requests
except ImportError:
    raise ImportError("'requests' not installed — run:  pip install requests python-dotenv")

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("'python-dotenv' not installed — run:  pip install requests python-dotenv")

# Load .env from the script's own directory
load_dotenv(Path(__file__).parent / ".env")


# ── Configuration (loaded from .env) ──────────────────────────────────────────

BASE_URL = "https://app.qfield.cloud/api/v1"
USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")

PROJECT_DIR = Path(__file__).parent
FILES_TO_UPLOAD = [
    PROJECT_DIR / "MapovaciFormularPS.qgs",
    PROJECT_DIR / "MapovaniePrePS.gpkg",
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def auth_headers(token: str) -> dict:
    return {"Authorization": f"token {token}"}


def api_get(token: str, path: str, params: dict = None) -> dict | list:
    r = requests.get(f"{BASE_URL}{path}", headers=auth_headers(token), params=params)
    r.raise_for_status()
    return r.json()


def api_post(token: str, path: str, payload: dict) -> dict:
    r = requests.post(f"{BASE_URL}{path}", headers=auth_headers(token), json=payload)
    r.raise_for_status()
    return r.json()


def upload_file(token: str, project_id: str, local_path: Path) -> None:
    url = f"{BASE_URL}/files/{project_id}/{local_path.name}/"
    with local_path.open("rb") as fh:
        r = requests.post(
            url,
            headers=auth_headers(token),
            files={"file": (local_path.name, fh, "application/octet-stream")},
        )
    if r.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed [{r.status_code}]: {r.text}")


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Validate config ───────────────────────────────────────────────────────
    if not USERNAME:
        raise RuntimeError("QFIELDCLOUD_USERNAME not set in .env")
    if not PASSWORD:
        raise RuntimeError("QFIELDCLOUD_PASSWORD not set in .env")

    missing = [p for p in FILES_TO_UPLOAD if not p.exists()]
    if missing:
        raise FileNotFoundError("Missing files:\n" + "\n".join(str(p) for p in missing))

    # ── Authenticate ─────────────────────────────────────────────────────────
    print(f"Logging in as {USERNAME}...")
    resp = requests.post(
        f"{BASE_URL}/auth/login/",
        json={"username": USERNAME, "password": PASSWORD},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed [{resp.status_code}]: {resp.text}")
    login_data = resp.json()
    token    = login_data["token"]
    username = login_data["username"]   # actual username returned by API, not the login email
    print(f"  OK (username: {username})")

    # ── Delete existing project if present ───────────────────────────────────
    print(f"\nChecking for existing project '{CLOUD_PROJECT_NAME}' (owner: {username})...")
    all_projects = api_get(token, "/projects/")
    existing = next((p for p in all_projects if p["owner"] == username and p["name"] == CLOUD_PROJECT_NAME), None)

    if existing:
        print(f"  Found: {existing['id']} — deleting...")
        r = requests.delete(
            f"{BASE_URL}/projects/{existing['id']}/",
            headers=auth_headers(token),
        )
        if r.status_code not in (200, 204):
            raise RuntimeError(f"Delete failed [{r.status_code}]: {r.text}")
        print("  Deleted.")
    else:
        print("  Not found — will create fresh.")

    # ── Create project ────────────────────────────────────────────────────────
    print(f"\nCreating project '{CLOUD_PROJECT_NAME}'...")
    p = api_post(token, "/projects/", {
        "name":        CLOUD_PROJECT_NAME,
        "owner":       username,
        "description": "Habitat mapping field forms (PS)",
        "is_public":   False,
    })
    project_id = p["id"]
    print(f"  Created: {project_id}")

    # ── Upload files ──────────────────────────────────────────────────────────
    print(f"\nUploading files to project {project_id}...")
    for path in FILES_TO_UPLOAD:
        size_mb = path.stat().st_size / 1_048_576
        print(f"  {path.name} ({size_mb:.1f} MB)...", end=" ", flush=True)
        upload_file(token, project_id, path)
        print("done")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n=== Upload complete ===")
    print(f"  https://app.qfield.cloud/{username}/{CLOUD_PROJECT_NAME}/")


if __name__ == "__main__":
    main()
