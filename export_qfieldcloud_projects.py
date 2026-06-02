"""
export_qfieldcloud_projects.py

Fetches all QFieldCloud projects (owned by the authenticated user) with their
collaborators and writes the result to an Excel file.

Requirements:
    pip install qfieldcloud-sdk python-dotenv openpyxl

Credentials are loaded from .env in the same directory as this script.

Usage:
    python export_qfieldcloud_projects.py
"""

import os
from pathlib import Path

try:
    from qfieldcloud_sdk import sdk
except ImportError:
    raise ImportError("'qfieldcloud-sdk' not installed — run:  pip install qfieldcloud-sdk python-dotenv openpyxl")

try:
    from dotenv import load_dotenv
except ImportError:
    raise ImportError("'python-dotenv' not installed — run:  pip install qfieldcloud-sdk python-dotenv openpyxl")

try:
    import openpyxl
    from openpyxl.styles import Font, PatternFill, Alignment
except ImportError:
    raise ImportError("'openpyxl' not installed — run:  pip install qfieldcloud-sdk python-dotenv openpyxl")

load_dotenv(Path(__file__).parent / ".env")

QFIELDCLOUD_URL = "https://app.qfield.cloud/api/v1/"
USERNAME = os.environ.get("QFIELDCLOUD_USERNAME", "")
PASSWORD = os.environ.get("QFIELDCLOUD_PASSWORD", "")
OUTPUT   = Path(__file__).parent / "qfieldcloud_projects.xlsx"


def extract_usernames(collaborators: list) -> list[str]:
    """Pull the username string out of whatever shape the SDK returns."""
    names = []
    for c in collaborators:
        if isinstance(c, str):
            names.append(c)
        elif isinstance(c, dict):
            # try common key locations
            name = (
                c.get("collaborator")
                or c.get("username")
                or (c.get("member") or {}).get("username")
            )
            if name:
                names.append(name)
    return names


def main() -> None:
    if not USERNAME:
        raise RuntimeError("QFIELDCLOUD_USERNAME not set in .env")
    if not PASSWORD:
        raise RuntimeError("QFIELDCLOUD_PASSWORD not set in .env")

    print("Logging in...")
    client = sdk.Client(url=QFIELDCLOUD_URL)
    client.login(USERNAME, PASSWORD)
    print(f"  OK")

    print("\nFetching projects...")
    projects = client.list_projects(include_public=False)
    print(f"  Found {len(projects)} project(s)")

    rows: list[tuple[str, str]] = []
    for p in projects:
        name       = p.get("name", "")
        project_id = p.get("id", "")
        print(f"  {name}...", end=" ", flush=True)
        try:
            collaborators = client.get_project_collaborators(project_id)
            names = extract_usernames(collaborators)
        except Exception as e:
            print(f"WARNING: {e}", end=" ")
            names = []
        print(f"{len(names)} collaborator(s)")
        rows.append((name, ", ".join(names)))

    # ── Write Excel ───────────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Projects"

    header_font  = Font(bold=True, color="FFFFFF")
    header_fill  = PatternFill("solid", fgColor="1F4E79")
    center_align = Alignment(vertical="center", wrap_text=True)

    for col, label in enumerate(["Project name", "Collaborators"], start=1):
        cell = ws.cell(row=1, column=col, value=label)
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = center_align

    ws.column_dimensions["A"].width = 25
    ws.column_dimensions["B"].width = 60

    for row_idx, (name, collabs) in enumerate(rows, start=2):
        ws.cell(row=row_idx, column=1, value=name).alignment  = center_align
        ws.cell(row=row_idx, column=2, value=collabs).alignment = center_align

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions

    wb.save(OUTPUT)
    print(f"\nSaved: {OUTPUT}  ({len(rows)} rows)")


if __name__ == "__main__":
    main()
