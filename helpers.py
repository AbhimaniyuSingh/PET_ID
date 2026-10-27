"""Small helpers: dates, vaccinations, photos, validation."""
import os
import re
import secrets
from datetime import date

from flask import request
from PIL import Image

from config import ALLOWED_EXT, MAX_VACC, UPLOAD_DIR
from db import get_db

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def base_url():
    """Public address used in QR codes and emails. Set BASE_URL in .env when you deploy."""
    return os.environ.get("BASE_URL", request.host_url).rstrip("/")


def digits_ok(phone):
    return len("".join(ch for ch in (phone or "") if ch.isdigit())) >= 10


def age_text(month, year):
    try:
        total = (date.today().year - int(year)) * 12 + (date.today().month - int(month))
    except (TypeError, ValueError):
        return "Unknown"
    if total < 0:
        return "Unknown"
    years, months = divmod(total, 12)
    parts = []
    if years:
        parts.append(f"{years} year{'s' if years != 1 else ''}")
    if months or not years:
        parts.append(f"{months} month{'s' if months != 1 else ''}")
    return " ".join(parts)


def nice_date(s):
    try:
        return date.fromisoformat((s or "")[:10]).strftime("%d %b %Y")
    except ValueError:
        return "—"


def valid_date(s):
    if not s:
        return True
    try:
        date.fromisoformat(s)
        return True
    except ValueError:
        return False


def vaccination_status(next_str):
    if not next_str:
        return None
    try:
        due = date.fromisoformat(next_str)
    except ValueError:
        return None
    days = (due - date.today()).days
    if days < 0:
        return ("overdue", f"Overdue by {-days} day{'s' if -days != 1 else ''}")
    if days <= 14:
        return ("soon", f"Due in {days} day{'s' if days != 1 else ''}")
    return ("ok", f"Due {due.strftime('%d %b %Y')}")


def map_query(pet):
    return ", ".join(x for x in (pet["area"], pet["city"]) if x)


def parse_coord(value, lo, hi):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if lo <= v <= hi else None


# ---------- photos ----------
def save_photo(file_storage):
    """Returns (filename or None, error or None)."""
    if not file_storage or not file_storage.filename:
        return None, None
    ext = file_storage.filename.rsplit(".", 1)[-1].lower()
    if ext not in ALLOWED_EXT:
        return None, "Photo must be a PNG, JPG or WEBP image."
    try:
        img = Image.open(file_storage.stream)
        img.verify()
    except Exception:
        return None, "That file is not a valid image."
    file_storage.stream.seek(0)
    filename = f"{secrets.token_hex(8)}.{ext}"
    file_storage.save(os.path.join(UPLOAD_DIR, filename))
    return filename, None


def delete_photo(filename):
    if filename:
        try:
            os.remove(os.path.join(UPLOAD_DIR, os.path.basename(filename)))
        except OSError:
            pass


# ---------- vaccinations ----------
def form_vacc_rows(form):
    """Rows exactly as typed (used to refill the form after an error)."""
    try:
        n = int(form.get("vacc_count", "0"))
    except ValueError:
        n = 0
    n = max(0, min(n, MAX_VACC))
    return [{"name": form.get(f"vacc_name_{i}", ""), "given": form.get(f"vacc_date_{i}", ""),
             "next": form.get(f"vacc_next_{i}", "")} for i in range(1, n + 1)]


def parse_vaccinations(form):
    rows, errors = [], []
    for i, r in enumerate(form_vacc_rows(form), start=1):
        name, given, nxt = r["name"].strip(), r["given"].strip(), r["next"].strip()
        if not name:
            errors.append(f"Vaccination {i}: please enter the vaccine name.")
            continue
        if not (valid_date(given) and valid_date(nxt)):
            errors.append(f"Vaccination {i}: dates must be valid dates.")
            continue
        rows.append((name[:100], given or None, nxt or None))
    return rows, errors


def save_vaccinations(db, pet_id, rows):
    """Replace a pet's vaccination list. A vet-verified record stays verified as long as the
    vaccine name and the date given are unchanged (so an owner can't edit a verified record
    into something different and keep the tick)."""
    old = {}
    for r in db.execute("SELECT * FROM vaccinations WHERE pet_id = ? AND verified_by IS NOT NULL",
                        (pet_id,)):
        old[(r["vaccine_name"].lower(), r["given_date"])] = r
    db.execute("DELETE FROM vaccinations WHERE pet_id = ?", (pet_id,))
    for name, given, nxt in rows:
        v = old.get((name.lower(), given))
        db.execute(
            "INSERT INTO vaccinations (pet_id, vaccine_name, given_date, next_due, verified_by, "
            "verified_at, verified_user_id) VALUES (?,?,?,?,?,?,?)",
            (pet_id, name, given, nxt, v["verified_by"] if v else None,
             v["verified_at"] if v else None, v["verified_user_id"] if v else None))


def vacc_rows_for_form(pet_id):
    rows = get_db().execute("SELECT * FROM vaccinations WHERE pet_id = ? ORDER BY id",
                            (pet_id,)).fetchall()
    return [{"name": r["vaccine_name"], "given": r["given_date"] or "",
             "next": r["next_due"] or ""} for r in rows]


def load_vaccinations(pet_id):
    rows = get_db().execute("SELECT * FROM vaccinations WHERE pet_id = ? ORDER BY id",
                            (pet_id,)).fetchall()
    return [{"id": r["id"], "name": r["vaccine_name"], "given": nice_date(r["given_date"]),
             "given_raw": r["given_date"], "next": nice_date(r["next_due"]),
             "next_raw": r["next_due"], "status": vaccination_status(r["next_due"]),
             "verified_by": r["verified_by"], "verified_at": nice_date(r["verified_at"])}
            for r in rows]


def next_due_summary(vaccs):
    best = None
    for v in vaccs:
        if v["status"] and v["status"][0] in ("soon", "ok"):
            d = date.fromisoformat(v["next_raw"])
            if best is None or d < best[0]:
                best = (d, v)
    return best[1] if best else None
