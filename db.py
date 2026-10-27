"""Database schema and connection helpers (SQLite)."""
import os
import sqlite3
from datetime import datetime

from flask import g

import config

DB_PATH = config.DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    name TEXT NOT NULL,
    phone TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'owner',          -- owner | vet | admin
    clinic_name TEXT,
    vet_reg_no TEXT,
    vet_approved INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS pets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL REFERENCES users(id),
    kind TEXT NOT NULL DEFAULT 'owned',          -- owned | community
    name TEXT NOT NULL,
    species TEXT NOT NULL,
    breed TEXT, gender TEXT, color TEXT,
    birth_month INTEGER, birth_year INTEGER,
    photo TEXT,
    microchip TEXT,
    sterilized TEXT DEFAULT 'Unknown',
    sterilized_date TEXT,
    temperament TEXT,
    medical_notes TEXT,
    food_notes TEXT,
    vet_name TEXT, vet_phone TEXT,
    area TEXT NOT NULL,
    city TEXT NOT NULL,
    caretaker_note TEXT,
    backup_name TEXT, backup_phone TEXT,
    show_phone INTEGER DEFAULT 0,
    show_notes INTEGER DEFAULT 1,
    show_vacc INTEGER DEFAULT 1,
    show_microchip INTEGER DEFAULT 0,
    show_map INTEGER DEFAULT 1,
    lost INTEGER DEFAULT 0,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS vaccinations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    vaccine_name TEXT NOT NULL,
    given_date TEXT,
    next_due TEXT,
    verified_by TEXT,
    verified_at TEXT,
    verified_user_id INTEGER
);
CREATE TABLE IF NOT EXISTS scans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    scanned_at TEXT NOT NULL,
    alerted INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    finder_name TEXT, finder_phone TEXT, location TEXT, message TEXT,
    lat REAL, lng REAL,
    created_at TEXT
);
CREATE TABLE IF NOT EXISTS reminders_sent (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vaccination_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    due_date TEXT NOT NULL,
    sent_at TEXT,
    UNIQUE (vaccination_id, kind, due_date)
);
CREATE TABLE IF NOT EXISTS transfers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    pet_id INTEGER NOT NULL REFERENCES pets(id) ON DELETE CASCADE,
    token TEXT UNIQUE NOT NULL,
    to_email TEXT NOT NULL,
    created_at TEXT,
    expires_at TEXT,
    status TEXT DEFAULT 'pending'               -- pending | accepted | cancelled
);
"""


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def connect_standalone():
    """Connection for background jobs that run outside a web request."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    # A database from the first two versions of PetID has a different layout: keep it as a
    # backup file and start fresh instead of crashing.
    if os.path.exists(DB_PATH):
        conn = sqlite3.connect(DB_PATH)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(pets)")]
        conn.close()
        if cols and "user_id" not in cols:
            backup = DB_PATH[:-3] + f"_old_{datetime.now():%Y%m%d_%H%M%S}.db"
            os.replace(DB_PATH, backup)
            print(f"[PetID] Old database moved to {backup}. Starting with a fresh one.")
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.commit()
    conn.close()
