"""
load_staff.py
-------------
Load (or refresh) the employee roster from data/staff.csv into the database.

Usage:
    python load_staff.py                 # uses data/staff.csv
    python load_staff.py path/to.csv     # custom file
    python load_staff.py --replace       # wipe staff table first

CSV columns (header row required): Employee_ID, Name, Department
Existing employee_ids are updated in place; new ones are inserted.
"""

import csv
import sys
from pathlib import Path

# Allow running directly from the project root (so `from app...` resolves).
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import SessionLocal, init_db  # noqa: E402
from app.models import Staff  # noqa: E402

DEFAULT_CSV = Path(__file__).resolve().parent / "data" / "staff.csv"


def load(csv_path: Path, replace: bool = False) -> None:
    init_db()  # make sure tables exist
    db = SessionLocal()
    try:
        if replace:
            deleted = db.query(Staff).delete()
            print(f"Cleared {deleted} existing staff rows.")

        inserted = updated = 0
        with csv_path.open(newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            required = {"Employee_ID", "Name", "Department"}
            if not required.issubset(set(reader.fieldnames or [])):
                raise SystemExit(
                    f"CSV must have columns {sorted(required)}; "
                    f"found {reader.fieldnames}"
                )

            for row in reader:
                emp_id = row["Employee_ID"].strip()
                if not emp_id:
                    continue
                existing = db.query(Staff).filter(Staff.employee_id == emp_id).first()
                if existing:
                    existing.name = row["Name"].strip()
                    existing.department = row["Department"].strip()
                    updated += 1
                else:
                    db.add(Staff(
                        employee_id=emp_id,
                        name=row["Name"].strip(),
                        department=row["Department"].strip(),
                    ))
                    inserted += 1

        db.commit()
        total = db.query(Staff).count()
        print(f"Done. Inserted {inserted}, updated {updated}. Roster now has {total} staff.")
    finally:
        db.close()


if __name__ == "__main__":
    args = [a for a in sys.argv[1:]]
    replace = "--replace" in args
    paths = [a for a in args if not a.startswith("--")]
    csv_path = Path(paths[0]) if paths else DEFAULT_CSV

    if not csv_path.exists():
        raise SystemExit(f"CSV not found: {csv_path}")

    print(f"Loading staff from {csv_path} (replace={replace})…")
    load(csv_path, replace=replace)
