"""SQLite conversation snapshots and a durable webhook response ledger."""

import json
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import time


class ConversationStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS conversations(
                    client_id TEXT PRIMARY KEY, state_json TEXT NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS events(
                    event_id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
                    state TEXT NOT NULL, response_json TEXT, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS leads(
                    lead_id TEXT PRIMARY KEY, client_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL, updated REAL NOT NULL, forwarded INTEGER NOT NULL DEFAULT 0);
            """)

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=15)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def load(self, client_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT state_json FROM conversations WHERE client_id=?", (client_id,)
            ).fetchone()
        return json.loads(row["state_json"]) if row else None

    def save(self, client_id, state):
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO conversations VALUES(?,?,?) ON CONFLICT(client_id) DO UPDATE SET state_json=excluded.state_json,updated=excluded.updated",
                (client_id, json.dumps(state, ensure_ascii=False), time.time()),
            )

    def forget(self, client_id):
        with self.connect() as connection:
            connection.execute("DELETE FROM conversations WHERE client_id=?", (client_id,))

    def event(self, event_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
        return dict(row) if row else None

    def reserve_event(self, event_id, client_id):
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO events VALUES(?,?,?,NULL,?)",
                (event_id, client_id, "processing", time.time()),
            )
            return cursor.rowcount == 1

    def save_event_response(self, event_id, response):
        with self.connect() as connection:
            connection.execute(
                "UPDATE events SET state=?,response_json=?,updated=? WHERE event_id=?",
                ("ready", json.dumps(response, ensure_ascii=False), time.time(), event_id),
            )

    def complete_event(self, event_id):
        with self.connect() as connection:
            connection.execute(
                "UPDATE events SET state=?,updated=? WHERE event_id=?",
                ("delivered", time.time(), event_id),
            )

    def fail_event(self, event_id):
        with self.connect() as connection:
            row = connection.execute(
                "SELECT response_json FROM events WHERE event_id=?", (event_id,)
            ).fetchone()
            if row and row["response_json"]:
                connection.execute(
                    "UPDATE events SET state=?,updated=? WHERE event_id=?",
                    ("ready", time.time(), event_id),
                )
            else:
                connection.execute(
                    "DELETE FROM events WHERE event_id=? AND state=?", (event_id, "processing")
                )

    def save_lead(self, lead_id, client_id, payload):
        with self.connect() as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO leads(lead_id,client_id,payload_json,updated) VALUES(?,?,?,?)",
                (lead_id, client_id, json.dumps(payload, ensure_ascii=False), time.time()),
            )
            return cursor.rowcount == 1

    def get_lead(self, lead_id):
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM leads WHERE lead_id=?", (lead_id,)).fetchone()
        return dict(row) if row else None

    def mark_lead_forwarded(self, lead_id):
        with self.connect() as connection:
            connection.execute(
                "UPDATE leads SET forwarded=1,updated=? WHERE lead_id=?", (time.time(), lead_id)
            )
