"""
Migration command for the OSS table.

Adds new columns required by the unified upload contract:
file_name, file_key, file_md5, file_size, extension, type

Usage: flask db migrate_oss
"""

from app.lin import db


MIGRATE_COLUMNS = [
    {"name": "file_name", "type": "VARCHAR(255)", "default": "''", "nullable": True},
    {"name": "file_key", "type": "VARCHAR(255)", "default": "''", "nullable": True},
    {"name": "file_md5", "type": "VARCHAR(40)", "default": "''", "nullable": True},
    {"name": "file_size", "type": "INTEGER", "default": None, "nullable": True},
    {"name": "extension", "type": "VARCHAR(50)", "default": None, "nullable": True},
    {"name": "type", "type": "VARCHAR(10)", "default": "'REMOTE'", "nullable": False},
]


def migrate_oss():
    """Add new columns to the oss table for unified upload contract."""
    inspector = db.inspect(db.engine)
    existing_columns = {col["name"] for col in inspector.get_columns("oss")}
    added = []
    skipped = []

    for col in MIGRATE_COLUMNS:
        if col["name"] in existing_columns:
            skipped.append(col["name"])
            continue

        nullable_clause = "" if col["nullable"] else " NOT NULL"
        default_clause = f" DEFAULT {col['default']}" if col["default"] is not None else ""
        sql = f"ALTER TABLE oss ADD COLUMN {col['name']} {col['type']}{nullable_clause}{default_clause}"

        try:
            db.session.execute(db.text(sql))
            added.append(col["name"])
        except Exception as e:
            print(f"  Warning: failed to add column {col['name']}: {e}")

    if added:
        db.session.commit()
        print(f"  Added columns: {', '.join(added)}")
    else:
        print("  No new columns to add.")

    if skipped:
        print(f"  Skipped (already exist): {', '.join(skipped)}")
