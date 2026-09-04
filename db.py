from pathlib import Path
import json, sqlite3
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "pricing_governance.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  analysis_date TEXT NOT NULL,
  account_count INTEGER NOT NULL,
  subscription_count INTEGER NOT NULL,
  validation_json TEXT NOT NULL,
  rows_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS scenarios (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  snapshot_id INTEGER NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  config_json TEXT NOT NULL,
  summary_json TEXT NOT NULL,
  FOREIGN KEY(snapshot_id) REFERENCES snapshots(id)
);
"""

def connect():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.executescript(SCHEMA)
    return c

def save_snapshot(name, analysis_date, rows, validation):
    account_count = len({r["account_key"] for r in rows})
    with connect() as c:
        cur = c.execute(
            "INSERT INTO snapshots(name,created_at,analysis_date,account_count,subscription_count,validation_json,rows_json) VALUES(?,?,?,?,?,?,?)",
            (name, datetime.now(timezone.utc).isoformat(), analysis_date, account_count, len(rows), json.dumps(validation), json.dumps(rows))
        )
        return cur.lastrowid

def get_snapshot(snapshot_id):
    with connect() as c:
        row = c.execute("SELECT * FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
    if not row: return None
    d = dict(row); d["validation"] = json.loads(d.pop("validation_json")); d["rows"] = json.loads(d.pop("rows_json")); return d

def list_snapshots():
    with connect() as c:
        rows = c.execute("SELECT id,name,created_at,analysis_date,account_count,subscription_count FROM snapshots ORDER BY id DESC LIMIT 20").fetchall()
    return [dict(r) for r in rows]

def save_scenario(snapshot_id, name, config, summary):
    with connect() as c:
        cur = c.execute("INSERT INTO scenarios(snapshot_id,name,created_at,config_json,summary_json) VALUES(?,?,?,?,?)",
            (snapshot_id, name, datetime.now(timezone.utc).isoformat(), json.dumps(config), json.dumps(summary)))
        return cur.lastrowid
