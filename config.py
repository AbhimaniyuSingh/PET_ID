"""Paths and constants shared by every module."""
import os

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

try:  # optional: read settings from a .env file next to app.py
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    pass

# Where the database and uploaded photos live. On a hosting service, point this at a
# persistent disk / volume (e.g. PETID_DATA_DIR=/var/data) or your data is lost on redeploy.
DATA_DIR = os.path.abspath(os.environ.get("PETID_DATA_DIR", BASE_DIR))
DB_PATH = os.path.join(DATA_DIR, "petid.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

ALLOWED_EXT = {"png", "jpg", "jpeg", "webp"}
MAX_VACC = 10
LANGS = ("en", "hi")
