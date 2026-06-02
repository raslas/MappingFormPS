"""
export_qfieldcloud_projects.py

Fetches all QFieldCloud projects owned by the authenticated user and writes
them to an Excel file.

NOTE: The QFieldCloud public API (v1) exposes only /projects/ and /jobs/.
There is no collaborators endpoint — that information is only available
through the web UI.

Requirements:
    pip install requests python-dotenv openpyxl

Credentials are loaded from .env in the same directory as this script.

Usage:
    python export_qfieldcloud_projects.py
"""

import os
from pathlib import Path

try:
    import requests
except ImportError:
    raise ImportError("'requests' not installed — run:  pip install requests python-dotenv openpyxl")

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("'python-dotenv' not installed — run:  pip install requests python-dotenv openpyxl")

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
except ImportError:
    raise ImportError("'openpyxl' not installed — run:  pip install requests python-dotenv openpyxl")

load_dotenv(Path(__file__).parent / ".env")

BASE_URL = "https://app.qfield.cloud/api/v1"
USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")
OUTPUT   = Path(__file__).parent / "qfieldcloud_projects.xlsx"

COLUMNS = [
    ("name",        "Project name",  25),
    ("description", "Description",   35),
    ("is_public",   "Public",        10),
    ("status",      "Status",        12),
    ("user_role",   "Your role",     15),
    ("created_at",  "Created",       20),
    ("updated_at",  "Updated",       20),
]


def auth_headers(token: str) -> dict:
    return {"Authorization": f"token {token}"}


def login() -> tuple[str, str]:
    if not USERNAME:
        raise RuntimeError("QFIELDCLOUD_USERNAME not set in .env")
    if not PASSWORD:
        raise RuntimeError("QFIELDCLOUD_PASSWORD not set in .env")
    r = requests.post(f"{BASE_URL}/auth/login/", json={"username": USERNAME, "password": PASSWORD})
    if r.status_code != 200:
        raise RuntimeError(f"Login failed [{r.status_code}]: {r.text}")
    data = r.json()
    print(f"  Logged in as: {data['username']}")
    return data["token"], data["username"]


def main() -> None:
    print("Logging in...")
    token, username = login()

    print("\nFetching projects...")
    r = requests.get(f"{BASE_URL}/projects/", headers=auth_headers(token))
    r.raise_for_status()
    all_projects = r.json()
    projects = [p for p in all_projects if p.get("owner") == username]
    print(f"  Found {len(projects)} project(s) owned by '{username}'")

    # ── Write Excel ───────────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Projects"

    header_font  = Font(bold=True, color="FFFFFF")
    header_fill  = PatternFill("solid", fgColor="1F4E79")
    center_align = Alignment(vertical="center", wrap_text=True)

    for col, (_, label, width) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col, value=label)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = center_align
        ws.column_dimensions[cell.column_letter].width = width

    for row_idx, p in enumerate(projects, start=2):
        for col, (field, _, _) in enumerate(COLUMNS, start=1):
            value = p.get(field, "")
            # Trim datetime to date only
            if isinstance(value, str) and "T" in value:
                value = value[:10]
            ws.cell(row=row_idx, column=col, value=value).alignment = center_align

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    wb.save(OUTPUT)
    print(f"\nSaved: {OUTPUT}  ({len(projects)} rows)")
    print("\nNote: collaborators are not exposed by the QFieldCloud REST API.")
    print("      Manage them at https://app.qfield.cloud/")


if __name__ == "__main__":
    main()
