"""
upload_to_qfieldcloud.py

Prepares a local GeoPackage and uploads the MapovaciFormularPS project to
app.qfield.cloud. Standalone Python script — no QGIS installation required.

Requirements:
    pip install requests python-dotenv

Credentials are loaded from .env in the same directory as this script.

Usage:
    1. Set CLOUD_PROJECT_NAME below to the target SKUEV code
    2. Fill in your credentials in .env
    3. python upload_to_qfieldcloud.py
"""

# ── Project name — change this to match your QFieldCloud project ───────────────

CLOUD_PROJECT_NAME = "SKUEV0870"

import os
import sqlite3
import struct
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

# ── Configuration ──────────────────────────────────────────────────────────────

BASE_URL = "https://app.qfield.cloud/api/v1"
USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")

PROJECT_DIR = Path(__file__).parent

# Local GeoPackage that will be cleared and repopulated, then uploaded
DEST_GPKG = PROJECT_DIR / "MapovaniePrePS.gpkg"

# Source GeoPackage containing all polygons across all SKUEV sites
SOURCE_GPKG  = Path(r"C:\_projects\MapovaniePrePS\MapovaniePrePS.gpkg")
SOURCE_LAYER = "podlaorta"
TARGET_LAYER = "tblHabHlavna"
COPY_FIELDS  = ["KOD_UEV", "podlaorta", "polygon_id", "p"]

FILES_TO_UPLOAD = [
    PROJECT_DIR / "MapovaciFormularPS.qgs",
    DEST_GPKG,
]

# ── GeoPackage helpers ─────────────────────────────────────────────────────────

def get_user_tables(conn: sqlite3.Connection) -> list[str]:
    """Return all non-system tables in the GeoPackage."""
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
        " AND name NOT LIKE 'gpkg_%'"
        " AND name NOT LIKE 'sqlite_%'"
        " AND name NOT LIKE 'rtree_%'"
    ).fetchall()
    return [r[0] for r in rows]


def parse_gpkg_bbox(blob: bytes) -> tuple | None:
    """
    Extract (minx, maxx, miny, maxy) from a GPKG geometry blob header.
    Returns None for null, empty, or envelopeless geometries.
    The GPKG header is always little-endian; envelope starts at byte 8.
    """
    if not blob or len(blob) < 40 or blob[0:2] != b'GP':
        return None
    flags = blob[3]
    if (flags >> 3) & 0x01:   # empty geometry flag
        return None
    if (flags & 0x07) == 0:   # no envelope stored in header
        return None
    return struct.unpack_from('<dddd', blob, 8)  # minx, maxx, miny, maxy


def prepare_local_gpkg() -> int:
    """
    1. Clear all data tables in DEST_GPKG (lookup tables are preserved).
    2. Copy features from SOURCE_LAYER where KOD_UEV = CLOUD_PROJECT_NAME
       into TARGET_LAYER (geometry + COPY_FIELDS only).
    3. Rebuild the spatial R-tree index.
    Returns the number of features inserted.
    """
    if not SOURCE_GPKG.exists():
        raise FileNotFoundError(f"Source GeoPackage not found: {SOURCE_GPKG}")

    print(f"\nPreparing local GeoPackage: {DEST_GPKG.name}")

    # ── Step 1: clear data tables (preserve lookups — they power form dropdowns) ─
    with sqlite3.connect(str(DEST_GPKG)) as conn:
        all_tables    = get_user_tables(conn)
        data_tables   = [t for t in all_tables if "lookup" not in t.lower()]
        lookup_tables = [t for t in all_tables if "lookup" in t.lower()]
        print(f"  Clearing {len(data_tables)} data table(s): {data_tables}")
        print(f"  Preserving {len(lookup_tables)} lookup table(s)")
        for table in data_tables:
            conn.execute(f'DELETE FROM "{table}"')
        for table in data_tables:
            conn.execute("DELETE FROM sqlite_sequence WHERE name = ?", (table,))
        conn.commit()
    print("  Data tables cleared.")

    # ── Step 2: fetch filtered features from source ───────────────────────────
    field_sql = ", ".join(f'"{f}"' for f in COPY_FIELDS)
    with sqlite3.connect(str(SOURCE_GPKG)) as src:
        rows = src.execute(
            f'SELECT geom, {field_sql} FROM "{SOURCE_LAYER}" WHERE "KOD_UEV" = ?',
            (CLOUD_PROJECT_NAME,),
        ).fetchall()

    if not rows:
        raise RuntimeError(
            f"No features found in '{SOURCE_LAYER}' where KOD_UEV = '{CLOUD_PROJECT_NAME}'"
        )

    # ── Step 3: insert features and rebuild spatial index ─────────────────────
    placeholders = ", ".join(["?"] * (len(COPY_FIELDS) + 1))  # +1 for geom
    rtree_table  = f"rtree_{TARGET_LAYER}_geom"

    with sqlite3.connect(str(DEST_GPKG)) as conn:
        # GPKG triggers call ST_IsEmpty() which plain sqlite3 doesn't support.
        # Save them, drop before insert, restore after.
        trigger_rows = conn.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (TARGET_LAYER,),
        ).fetchall()
        for name, _ in trigger_rows:
            conn.execute(f'DROP TRIGGER IF EXISTS "{name}"')

        conn.executemany(
            f'INSERT INTO "{TARGET_LAYER}" (geom, {field_sql}) VALUES ({placeholders})',
            rows,
        )

        # Rebuild R-tree spatial index from geometry blob headers
        conn.execute(f'DELETE FROM "{rtree_table}"')
        inserted = conn.execute(
            f'SELECT fid, geom FROM "{TARGET_LAYER}"'
        ).fetchall()
        rtree_entries = [
            (fid, *bbox)
            for fid, geom_blob in inserted
            if (bbox := parse_gpkg_bbox(geom_blob)) is not None
        ]
        if rtree_entries:
            conn.executemany(
                f'INSERT INTO "{rtree_table}" (id, minx, maxx, miny, maxy) VALUES (?,?,?,?,?)',
                rtree_entries,
            )

        # Restore triggers
        for _, sql in trigger_rows:
            conn.execute(sql)

        conn.commit()

    # Force WAL checkpoint: merges the .gpkg-wal sidecar back into the main
    # .gpkg file so the uploaded file contains all changes, not just the old data.
    with sqlite3.connect(str(DEST_GPKG)) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

    print(f"  Inserted {len(rows)} features, rebuilt spatial index ({len(rtree_entries)} entries).")
    return len(rows)


# ── QFieldCloud API helpers ────────────────────────────────────────────────────

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

    # ── Prepare local GeoPackage ──────────────────────────────────────────────
    prepare_local_gpkg()

    # ── Authenticate ─────────────────────────────────────────────────────────
    print(f"\nLogging in as {USERNAME}...")
    resp = requests.post(
        f"{BASE_URL}/auth/login/",
        json={"username": USERNAME, "password": PASSWORD},
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed [{resp.status_code}]: {resp.text}")
    login_data = resp.json()
    token    = login_data["token"]
    username = login_data["username"]
    print(f"  OK (username: {username})")

    # ── Delete existing cloud project if present ──────────────────────────────
    print(f"\nChecking for existing project '{CLOUD_PROJECT_NAME}' (owner: {username})...")
    all_projects = api_get(token, "/projects/")
    existing = next(
        (p for p in all_projects if p["owner"] == username and p["name"] == CLOUD_PROJECT_NAME),
        None,
    )

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
