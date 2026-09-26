"""Consistent SQLite backup; keep SETTINGS_ENCRYPTION_KEY separately with the backup."""
import argparse
import sqlite3
from pathlib import Path
from dotenv import load_dotenv
import os

load_dotenv()
parser=argparse.ArgumentParser()
parser.add_argument("destination",type=Path)
args=parser.parse_args()
source=Path(os.getenv("DATA_DIR","data"))/"alerts.sqlite3"
if not source.exists():
    raise SystemExit("Database does not exist.")
if args.destination.exists():
    raise SystemExit("Destination exists; choose a new backup path.")
args.destination.parent.mkdir(parents=True,exist_ok=True)
with sqlite3.connect(source) as src, sqlite3.connect(args.destination) as dst:
    src.backup(dst)
print("Backup complete. Keep the matching SETTINGS_ENCRYPTION_KEY separately.")
