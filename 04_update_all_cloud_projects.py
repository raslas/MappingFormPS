"""
04_update_all_cloud_projects.py

Pushes the updated MapovaciFormularPS.qgs and lookup GeoPackages to ALL
existing QFieldCloud projects whose name matches SKUEV???? .

DATA-SAFE by design:
  - MapovaniePrePS.gpkg (the mapping data) is NEVER uploaded or touched.
  - Lookup GeoPackages are uploaded only when missing in the cloud or when
    their MD5 differs from the local file.
  - Per-project settings inside the cloud .qgs (map canvas extent and the
    QFieldSync area of interest) are read from the cloud copy first and
    transplanted into the new local .qgs before upload, so every project
    keeps its own extent/AOI.

Requirements:
    pip install requests python-dotenv

Credentials are loaded from .env in the same directory:
    QFIELDCLOUD_USERNAME=...
    QFIELDCLOUD_PASSWORD=...

Optional:
    QFIELDCLOUD_TOKEN=...
    QFIELDCLOUD_OWNER=...

Usage:
    python 04_update_all_cloud_projects.py --dry-run
    python 04_update_all_cloud_projects.py
    python 04_update_all_cloud_projects.py --skuev SKUEV0332
    python 04_update_all_cloud_projects.py --skuev SKUEV0332 --dry-run
"""

import argparse
import fnmatch
import hashlib
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from xml.sax.saxutils import escape

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
QGS_FILE = PROJECT_DIR / "MapovaciFormularPS.qgs"

# Lookup GeoPackages to push (uploaded only if missing or changed in the cloud).
# MapovaniePrePS.gpkg must NEVER appear here — it holds the collected field data.
LOOKUP_FILES = [
    PROJECT_DIR / "PrevodKatalogy.gpkg",
    PROJECT_DIR / "AktivityLookup.gpkg",
]

PROJECT_NAME_PATTERN = "SKUEV????"   # fnmatch pattern: SKUEV + any 4 characters

BASE_URL = "https://app.qfield.cloud/api/v1"

load_dotenv(PROJECT_DIR / ".env")

USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")
TOKEN = os.environ.get("QFIELDCLOUD_TOKEN", "")
OWNER = os.environ.get("QFIELDCLOUD_OWNER", "")


# -- API helpers ----------------------------------------------------------------

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


def download_file(token: str, project_id: str, filename: str) -> bytes:
    resp = requests.get(
        f"{BASE_URL}/files/{project_id}/{filename}/",
        headers=auth_headers(token),
        timeout=300,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Download of {filename} failed [{resp.status_code}]: {resp.text}")
    return resp.content


def upload_bytes(token: str, project_id: str, filename: str, data: bytes) -> None:
    resp = requests.post(
        f"{BASE_URL}/files/{project_id}/{filename}/",
        headers=auth_headers(token),
        files={"file": (filename, data, "application/octet-stream")},
        timeout=300,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload of {filename} failed [{resp.status_code}]: {resp.text}")


def upload_path(token: str, project_id: str, local_path: Path) -> None:
    with local_path.open("rb") as fh:
        resp = requests.post(
            f"{BASE_URL}/files/{project_id}/{local_path.name}/",
            headers=auth_headers(token),
            files={"file": (local_path.name, fh, "application/octet-stream")},
            timeout=300,
        )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload of {local_path.name} failed [{resp.status_code}]: {resp.text}")


def md5_of(path: Path) -> str:
    h = hashlib.md5()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# -- .qgs per-project settings transplant ----------------------------------------

def extract_remote_settings(remote_xml: str) -> dict:
    """
    Pull the per-project values out of a cloud .qgs file:
      - <mapcanvas name="theMapCanvas"><extent> xmin/ymin/xmax/ymax
      - areaOfInterest + areaOfInterestCrs from the <QFieldSync> and
        <qfieldsync> property sections
    Missing pieces are simply omitted (the local value is then kept).
    """
    settings: dict = {"extent": None, "aoi": {}}
    tree = ET.fromstring(remote_xml)

    canvas = next(
        (el for el in tree.iter("mapcanvas") if el.get("name") == "theMapCanvas"),
        None,
    )
    if canvas is not None:
        ext = canvas.find("extent")
        if ext is not None:
            vals = {}
            for tag in ("xmin", "ymin", "xmax", "ymax"):
                el = ext.find(tag)
                if el is None or el.text is None:
                    vals = None
                    break
                vals[tag] = el.text.strip()
            settings["extent"] = vals

    props = tree.find("properties")
    if props is not None:
        for section in ("QFieldSync", "qfieldsync"):
            sec = props.find(section)
            if sec is None:
                continue
            entry = {}
            for tag in ("areaOfInterest", "areaOfInterestCrs"):
                el = sec.find(tag)
                if el is not None and el.text:
                    entry[tag] = el.text
            if entry:
                settings["aoi"][section] = entry

    return settings


def patch_local_qgs(local_text: str, settings: dict) -> tuple[str, list[str]]:
    """
    Return a copy of the local .qgs text with the remote per-project settings
    substituted in. Pure text substitution — nothing else in the file changes.
    Also returns a list of warnings for pieces that could not be applied.
    """
    text = local_text
    warnings: list[str] = []

    if settings["extent"]:
        extent_vals = settings["extent"]

        def repl_extent(m: re.Match) -> str:
            inner = m.group(2)
            for tag, val in extent_vals.items():
                inner = re.sub(
                    rf"<{tag}>[^<]*</{tag}>",
                    lambda _m, t=tag, v=val: f"<{t}>{v}</{t}>",
                    inner,
                    count=1,
                )
            return m.group(1) + inner + m.group(3)

        text, n = re.subn(
            r'(<mapcanvas name="theMapCanvas"[^>]*>.*?<extent>)(.*?)(</extent>)',
            repl_extent,
            text,
            count=1,
            flags=re.DOTALL,
        )
        if n == 0:
            warnings.append("theMapCanvas extent not found in local .qgs - kept local value")
    else:
        warnings.append("no extent in cloud .qgs - kept local value")

    if not settings["aoi"]:
        warnings.append("no areaOfInterest in cloud .qgs - kept local value")

    for section, vals in settings["aoi"].items():

        def repl_block(m: re.Match, section_vals=vals) -> str:
            inner = m.group(2)
            for tag, val in section_vals.items():
                inner, k = re.subn(
                    rf'<{tag} type="QString">[^<]*</{tag}>',
                    lambda _m, t=tag, v=val: f'<{t} type="QString">{escape(v)}</{t}>',
                    inner,
                    count=1,
                )
                if k == 0:
                    warnings.append(f"<{tag}> not found in local <{m.group(1)[1:-1]}> section")
            return m.group(1) + inner + m.group(3)

        text, n = re.subn(
            rf"(<{section}>)(.*?)(</{section}>)",
            repl_block,
            text,
            count=1,
            flags=re.DOTALL,
        )
        if n == 0:
            warnings.append(f"<{section}> section not found in local .qgs - kept local value")

    return text, warnings


# -- Main ------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Update the .qgs project file and lookup GeoPackages in all "
            f"QFieldCloud projects matching '{PROJECT_NAME_PATTERN}'. "
            "Never uploads MapovaniePrePS.gpkg."
        )
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be uploaded, but do not upload anything.",
    )
    parser.add_argument(
        "--skuev",
        metavar="NAME",
        help=(
            "Update only the given project (e.g. --skuev SKUEV0332). "
            "Wildcards are allowed (e.g. --skuev 'SKUEV03??'). "
            f"Default: all projects matching {PROJECT_NAME_PATTERN}."
        ),
    )
    args = parser.parse_args()

    name_pattern = args.skuev.strip() if args.skuev else PROJECT_NAME_PATTERN

    if not QGS_FILE.exists():
        raise FileNotFoundError(f"Project file not found: {QGS_FILE}")
    missing_lookups = [p for p in LOOKUP_FILES if not p.exists()]
    if missing_lookups:
        raise FileNotFoundError(
            "Missing lookup file(s):\n" + "\n".join(str(p) for p in missing_lookups)
        )

    local_qgs_text = QGS_FILE.read_text(encoding="utf-8")
    lookup_md5 = {p.name: md5_of(p) for p in LOOKUP_FILES}

    mode = "DRY RUN" if args.dry_run else "UPLOAD"
    print(f"Mode: {mode}")
    print(f"Project file: {QGS_FILE.name}")
    print(f"Lookup files: {', '.join(p.name for p in LOOKUP_FILES)}")
    print(f"Project name pattern: {name_pattern}")

    print(f"\nLogging in as {USERNAME or '(token user)'}...")
    token, username = login()
    preferred_owner = OWNER or username
    print(f"  OK (preferred owner: {preferred_owner})")

    print("\nFetching QFieldCloud projects...")
    all_projects = api_get(token, "/projects/")

    # name -> project, preferring projects owned by preferred_owner
    matched: dict[str, dict] = {}
    for project in all_projects:
        name = project.get("name", "")
        if not fnmatch.fnmatchcase(name, name_pattern):
            continue
        existing = matched.get(name)
        if existing is None or (
            project.get("owner") == preferred_owner
            and existing.get("owner") != preferred_owner
        ):
            matched[name] = project

    if not matched:
        print(f"No projects matching '{name_pattern}' found.")
        return

    names = sorted(matched)
    print(f"\nMatched {len(names)} project(s):")
    for name in names:
        print(f"  - {name} (owner: {matched[name].get('owner')})")

    if not args.dry_run:
        print(
            f"\nThis will upload {QGS_FILE.name} (with per-project extent/AOI "
            f"preserved) and any changed lookup file(s) to the {len(names)} "
            f"project(s) above.\nMapovaniePrePS.gpkg is NOT touched."
        )
        answer = input("Continue? [yes/no]: ").strip().lower()
        if answer not in ("yes", "y", "ok"):
            print("Aborted.")
            return

    updated: list[str] = []
    failed: list[str] = []

    for name in names:
        project = matched[name]
        project_id = project["id"]
        print(f"\n=== {name} " + "=" * max(0, 60 - len(name)))

        try:
            remote_files = {f["name"]: f for f in api_get(token, f"/files/{project_id}/")}

            # -- .qgs: transplant per-project settings, then upload -------------
            if QGS_FILE.name in remote_files:
                remote_xml = download_file(token, project_id, QGS_FILE.name).decode("utf-8")
                settings = extract_remote_settings(remote_xml)
                patched, warnings = patch_local_qgs(local_qgs_text, settings)
                for w in warnings:
                    print(f"  ! {w}")
            else:
                print(f"  ! {QGS_FILE.name} not found in cloud - uploading local file as-is")
                patched = local_qgs_text

            if args.dry_run:
                print(f"  would upload: {QGS_FILE.name}")
            else:
                upload_bytes(token, project_id, QGS_FILE.name, patched.encode("utf-8"))
                print(f"  uploaded: {QGS_FILE.name}")

            # -- lookups: upload only when missing or changed --------------------
            for lookup in LOOKUP_FILES:
                remote = remote_files.get(lookup.name)
                if remote is not None and remote.get("md5sum") == lookup_md5[lookup.name]:
                    print(f"  up to date: {lookup.name}")
                    continue
                reason = "missing in cloud" if remote is None else "changed"
                if args.dry_run:
                    print(f"  would upload: {lookup.name} ({reason})")
                else:
                    upload_path(token, project_id, lookup)
                    print(f"  uploaded: {lookup.name} ({reason})")

        except Exception as exc:
            print(f"  FAILED: {exc}")
            failed.append(name)
            continue

        updated.append(name)

    print("\n" + "=" * 64)
    verb = "Would update" if args.dry_run else "Updated"
    print(f"{verb} {len(updated)} project(s).")
    if failed:
        print(f"Failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
