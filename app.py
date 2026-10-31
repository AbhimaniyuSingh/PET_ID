"""
PetID - pet identity site with QR tags.   Run:  python app.py   ->   http://127.0.0.1:5000

Files:  config.py (paths) | db.py (tables) | security.py (login, CSRF, CAPTCHA, rate limits)
        notify.py (email/SMS/WhatsApp) | pdfs.py (tag, poster, health card)
        i18n.py (English/Hindi) | tasks.py (vaccination reminders) | helpers.py
"""
import calendar
import os
import secrets
from datetime import date, datetime, timedelta
from urllib.parse import urlparse

from flask import (Flask, abort, flash, jsonify, redirect, render_template, request,
                   send_file, send_from_directory, session, url_for)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

import config
import notify
import pdfs
import tasks
from config import MAX_VACC, UPLOAD_DIR
from db import close_db, get_db, init_db
from helpers import (EMAIL_RE, age_text, base_url, delete_photo, digits_ok, form_vacc_rows,
                     load_vaccinations, map_query, next_due_summary, parse_coord,
                     parse_vaccinations, save_photo, save_vaccinations, valid_date,
                     vacc_rows_for_form)
from i18n import current_lang, t
from security import (admin_required, captcha_ok, captcha_question, check_csrf, csrf_token,
                      current_user, honeypot_tripped, ip, login_required, login_user,
                      rate_limited, safe_equal, safe_next)

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-change-me-in-production"),
    MAX_CONTENT_LENGTH=5 * 1024 * 1024,                      # 5 MB uploads
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("BASE_URL", "").startswith("https"),
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)
if os.environ.get("TRUST_PROXY") == "1":   # set this on hosts like Render/Railway (they sit behind a proxy)
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
if app.config["SECRET_KEY"] == "dev-change-me-in-production":
    print("[PetID] WARNING: using the default SECRET_KEY. Set SECRET_KEY in .env before deploying.")

app.teardown_appcontext(close_db)
app.before_request(check_csrf)
app.jinja_env.globals.update(csrf_token=csrf_token, captcha_question=captcha_question, t=t,
                             current_user=current_user, current_lang=current_lang)

MONTHS = [(i, calendar.month_name[i]) for i in range(1, 13)]
SPECIES = ["Dog", "Cat", "Rabbit", "Bird", "Other"]
reset_serializer = URLSafeTimedSerializer(app.config["SECRET_KEY"], salt="password-reset")


@app.after_request
def security_headers(resp):
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "SAMEORIGIN"
    if os.environ.get("BASE_URL", "").startswith("https"):
        resp.headers["Strict-Transport-Security"] = "max-age=31536000"
    return resp


@app.errorhandler(400)
@app.errorhandler(403)
@app.errorhandler(404)
@app.errorhandler(413)
def http_error(err):
    messages = {400: "Something was wrong with that request.", 403: "You don't have access to this page.",
                404: "We couldn't find that page.", 413: "That file is too large (max 5 MB)."}
    detail = err.description if err.code == 400 else messages.get(err.code, "")
    return render_template("error.html", code=err.code, message=detail), err.code


def now_iso():
    return datetime.now().isoformat(timespec="seconds")


# =====================================================================
#  Public pages
# =====================================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/lang/<code>")
def set_lang(code):
    if code in config.LANGS:
        session["lang"] = code
    ref = urlparse(request.referrer or "")
    target = safe_next(ref.path + ("?" + ref.query if ref.query else ""))
    return redirect(target or url_for("index"))


@app.route("/uploads/<path:filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_DIR, filename)


@app.route("/manifest.webmanifest")
def manifest():
    return send_from_directory(app.static_folder, "manifest.webmanifest",
                               mimetype="application/manifest+json")


@app.route("/sw.js")
def service_worker():
    resp = send_from_directory(app.static_folder, "sw.js", mimetype="application/javascript")
    resp.headers["Service-Worker-Allowed"] = "/"
    resp.headers["Cache-Control"] = "no-cache"
    return resp


def get_pet_public(public_id):
    pet = get_db().execute("SELECT * FROM pets WHERE public_id = ?", (public_id,)).fetchone()
    if pet is None:
        abort(404)
    return pet


def render_pet(pet):
    owner = get_db().execute("SELECT name, phone FROM users WHERE id = ?", (pet["user_id"],)).fetchone()
    vaccs = load_vaccinations(pet["id"])
    return render_template(
        "pet.html", pet=pet, owner=owner,
        age=age_text(pet["birth_month"], pet["birth_year"]),
        birth_month_name=calendar.month_name[pet["birth_month"]] if pet["birth_month"] else None,
        vaccs=vaccs, next_due=next_due_summary(vaccs), map_query=map_query(pet))


def should_alert(db, pet_id):
    try:
        cooldown = int(os.environ.get("SCAN_ALERT_COOLDOWN", "60"))
    except ValueError:
        cooldown = 60
    if cooldown <= 0:
        return True
    row = db.execute("SELECT scanned_at FROM scans WHERE pet_id = ? AND alerted = 1 "
                     "ORDER BY id DESC LIMIT 1", (pet_id,)).fetchone()
    if not row:
        return True
    return (datetime.now() - datetime.fromisoformat(row["scanned_at"])).total_seconds() >= cooldown


def log_scan_and_alert(pet):
    db = get_db()
    owner = db.execute("SELECT * FROM users WHERE id = ?", (pet["user_id"],)).fetchone()
    now = datetime.now()
    # alert unless the same pet was just alerted, or this visitor is scanning a flood of tags
    alert = should_alert(db, pet["id"]) and not rate_limited(f"scanalert:{ip()}", 20, 3600)
    db.execute("INSERT INTO scans (pet_id, scanned_at, alerted) VALUES (?,?,?)",
               (pet["id"], now.isoformat(timespec="seconds"), 1 if alert else 0))
    db.commit()
    if not alert:
        return
    link = base_url() + url_for("manage", pet_id=pet["id"])
    body = (f"Hi {owner['name']},\n\nSomeone just scanned the PetID tag of {pet['name']} on "
            f"{now.strftime('%d %b %Y at %I:%M %p')}.\n\n"
            f"If {pet['name']} is missing, log in to see messages from the finder:\n{link}\n\n"
            f"If you scanned the tag yourself, you can ignore this message.\n")
    sms = (f"PetID: {pet['name']}'s QR tag was scanned at {now.strftime('%I:%M %p, %d %b')}. "
           f"Check your email for details.")
    phones = [owner["phone"]] + ([pet["backup_phone"]] if pet["backup_phone"] else [])
    notify.notify_async(owner["email"], phones, f"PetID alert: {pet['name']}'s tag was just scanned",
                        body, sms)


@app.route("/p/<public_id>")
def pet_page(public_id):
    pet = get_pet_public(public_id)
    log_scan_and_alert(pet)              # every scan is logged; owner gets email + SMS
    return render_pet(pet)


@app.route("/p/<public_id>/report", methods=["POST"])
def report(public_id):
    pet = get_pet_public(public_id)
    f = request.form
    back = redirect(url_for("pet_page", public_id=public_id))
    if honeypot_tripped(f):
        return back                                      # silently drop bots
    if rate_limited(f"report:{ip()}:{pet['id']}", 5, 3600) or rate_limited(f"report:{ip()}", 15, 3600):
        flash("Too many messages from your network. Please try again later.", "error")
        return back
    if not captcha_ok(f):
        flash("The quick check answer was wrong. Please try again.", "error")
        return back
    message = f.get("message", "").strip()
    phone = f.get("finder_phone", "").strip()
    if not message and not phone:
        flash("Please add a message or your phone number so the owner can reach you.", "error")
        return back
    finder = f.get("finder_name", "").strip()[:100]
    location = f.get("location", "").strip()[:200]
    lat, lng = parse_coord(f.get("lat"), -90, 90), parse_coord(f.get("lng"), -180, 180)
    if lat is None or lng is None:
        lat = lng = None
    db = get_db()
    db.execute("INSERT INTO reports (pet_id, finder_name, finder_phone, location, message, lat, lng, "
               "created_at) VALUES (?,?,?,?,?,?,?,?)",
               (pet["id"], finder, phone[:30], location, message[:1000], lat, lng,
                datetime.now().strftime("%d %b %Y, %H:%M")))
    db.commit()
    owner = db.execute("SELECT * FROM users WHERE id = ?", (pet["user_id"],)).fetchone()
    gps = f"https://www.google.com/maps?q={lat},{lng}" if lat is not None else "not shared"
    body = (f"Hi {owner['name']},\n\nSomeone found {pet['name']} and left you a message:\n\n"
            f"Name: {finder or 'not given'}\nPhone: {phone or 'not given'}\n"
            f"Found at: {location or 'not given'}\nExact location: {gps}\nMessage: {message or '-'}\n")
    sms = (f"PetID: someone found {pet['name']}! Call {phone}." if phone
           else f"PetID: someone found {pet['name']} and left a message. Check your email.")
    phones = [owner["phone"]] + ([pet["backup_phone"]] if pet["backup_phone"] else [])
    notify.notify_async(owner["email"], phones, f"PetID: someone found {pet['name']}!", body, sms)
    flash("Thank you! The owner has been notified.", "ok")
    return back


@app.route("/community")
def community():
    db = get_db()
    city = request.args.get("city", "").strip()
    cities = [r[0] for r in db.execute(
        "SELECT DISTINCT city FROM pets WHERE kind = 'community' ORDER BY city")]
    sql, params = "SELECT * FROM pets WHERE kind = 'community'", []
    if city:
        sql += " AND city = ? COLLATE NOCASE"
        params.append(city)
    pets = db.execute(sql + " ORDER BY city, area, name LIMIT 300", params).fetchall()
    groups = {}
    for p in pets:
        groups.setdefault((p["city"], p["area"]), []).append(p)
    return render_template("community.html", groups=groups, cities=cities, city=city)


# =====================================================================
#  Accounts
# =====================================================================
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "GET":
        return render_template("signup.html", form={})
    f = request.form
    if honeypot_tripped(f):
        abort(400)
    errors = []
    if rate_limited(f"signup:{ip()}", 8, 3600):
        errors.append("Too many sign-ups from your network. Please try again later.")
    if not captcha_ok(f):
        errors.append("The quick check answer was wrong.")
    name, email = f.get("name", "").strip(), f.get("email", "").strip().lower()
    phone, password = f.get("phone", "").strip(), f.get("password", "")
    role = f.get("role", "owner")
    if role not in ("owner", "vet"):
        role = "owner"
    if not name:
        errors.append("Your name is required.")
    if not EMAIL_RE.match(email):
        errors.append("Enter a valid email address.")
    if not digits_ok(phone):
        errors.append("Enter a valid phone number (at least 10 digits).")
    if len(password) < 8:
        errors.append("Password must be at least 8 characters.")
    clinic, reg_no = f.get("clinic_name", "").strip(), f.get("vet_reg_no", "").strip()
    if role == "vet" and not (clinic and reg_no):
        errors.append("Vets must enter the clinic name and registration number.")
    db = get_db()
    if EMAIL_RE.match(email) and db.execute("SELECT 1 FROM users WHERE email = ?", (email,)).fetchone():
        errors.append("An account with this email already exists. Try logging in.")
    if errors:
        for e in errors:
            flash(e, "error")
        return render_template("signup.html", form=f), 400
    if email == os.environ.get("ADMIN_EMAIL", "").strip().lower():
        role = "admin"
    cur = db.execute(
        "INSERT INTO users (email, password_hash, name, phone, role, clinic_name, vet_reg_no, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (email, generate_password_hash(password), name[:100], phone[:30], role,
         clinic[:120] or None, reg_no[:60] or None, now_iso()))
    db.commit()
    login_user(cur.lastrowid)
    if role == "vet":
        flash("Account created. An admin must approve your vet account before you can verify records.", "ok")
        return redirect(url_for("vet_home"))
    flash("Welcome to PetID! Add your first pet below.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "GET":
        return render_template("login.html", next=request.args.get("next", ""))
    f = request.form
    if rate_limited(f"login:{ip()}", 10, 600):
        flash("Too many login attempts. Please wait a few minutes.", "error")
        return render_template("login.html", next=f.get("next", "")), 429
    user = get_db().execute("SELECT * FROM users WHERE email = ?",
                            (f.get("email", "").strip().lower(),)).fetchone()
    if not user or not check_password_hash(user["password_hash"], f.get("password", "")):
        flash("Wrong email or password.", "error")
        return render_template("login.html", next=f.get("next", "")), 401
    login_user(user["id"])
    return redirect(safe_next(f.get("next")) or url_for("dashboard"))


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("You are logged out.", "ok")
    return redirect(url_for("index"))


@app.route("/forgot", methods=["GET", "POST"])
def forgot():
    if request.method == "GET":
        return render_template("forgot.html")
    if rate_limited(f"forgot:{ip()}", 5, 3600):
        flash("Too many requests. Please try again later.", "error")
        return render_template("forgot.html"), 429
    email = request.form.get("email", "").strip().lower()
    user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    if user:
        token = reset_serializer.dumps({"uid": user["id"], "h": user["password_hash"][-12:]})
        link = base_url() + url_for("reset_password", token=token)
        notify.notify_async(user["email"], [], "Reset your PetID password",
                            f"Hi {user['name']},\n\nUse this link within 1 hour to choose a new password:\n"
                            f"{link}\n\nIf you didn't ask for this, ignore this email.\n")
    flash("If that email is registered, a reset link has been sent.", "ok")   # same message either way
    return redirect(url_for("login"))


@app.route("/reset/<token>", methods=["GET", "POST"])
def reset_password(token):
    try:
        data = reset_serializer.loads(token, max_age=3600)
    except (BadSignature, SignatureExpired):
        flash("That reset link is invalid or has expired.", "error")
        return redirect(url_for("forgot"))
    db = get_db()
    user = db.execute("SELECT * FROM users WHERE id = ?", (data["uid"],)).fetchone()
    if not user or user["password_hash"][-12:] != data["h"]:      # link already used
        flash("That reset link is invalid or has already been used.", "error")
        return redirect(url_for("forgot"))
    if request.method == "GET":
        return render_template("reset.html")
    password = request.form.get("password", "")
    if len(password) < 8:
        flash("Password must be at least 8 characters.", "error")
        return render_template("reset.html"), 400
    db.execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(password), user["id"]))
    db.commit()
    flash("Password changed. You can log in now.", "ok")
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    user, db = current_user(), get_db()
    if request.method == "POST":
        f = request.form
        if f.get("action") == "profile":
            name, phone = f.get("name", "").strip(), f.get("phone", "").strip()
            if not name or not digits_ok(phone):
                flash("Enter your name and a valid phone number.", "error")
            else:
                db.execute("UPDATE users SET name = ?, phone = ? WHERE id = ?", (name[:100], phone[:30], user["id"]))
                db.commit()
                flash("Profile updated. Alerts will go to the new number.", "ok")
        elif f.get("action") == "password":
            if not check_password_hash(user["password_hash"], f.get("current", "")):
                flash("Current password is wrong.", "error")
            elif len(f.get("new", "")) < 8:
                flash("New password must be at least 8 characters.", "error")
            else:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (generate_password_hash(f["new"]), user["id"]))
                db.commit()
                flash("Password changed.", "ok")
        return redirect(url_for("account"))
    return render_template("account.html")


# =====================================================================
#  Owner area: dashboard, add/edit/delete pets
# =====================================================================
@app.route("/dashboard")
@login_required
def dashboard():
    user, db = current_user(), get_db()
    if user["role"] == "vet":
        return redirect(url_for("vet_home"))
    pets = db.execute("SELECT * FROM pets WHERE user_id = ? ORDER BY id DESC", (user["id"],)).fetchall()
    incoming = db.execute(
        "SELECT t.token, p.name AS pet_name, u.name AS from_name FROM transfers t "
        "JOIN pets p ON p.id = t.pet_id JOIN users u ON u.id = p.user_id "
        "WHERE lower(t.to_email) = lower(?) AND t.status = 'pending' AND t.expires_at > ?",
        (user["email"], now_iso())).fetchall()
    return render_template("dashboard.html", pets=pets, incoming=incoming)


TEXT_FIELDS = ["kind", "name", "species", "breed", "gender", "color", "birth_month", "birth_year",
               "microchip", "sterilized", "sterilized_date", "temperament", "medical_notes",
               "food_notes", "vet_name", "vet_phone", "area", "city", "caretaker_note",
               "backup_name", "backup_phone"]
PRIVACY_FIELDS = ["show_phone", "show_notes", "show_vacc", "show_microchip", "show_map"]
LONG_FIELDS = {"medical_notes": 1000, "food_notes": 600, "caretaker_note": 300, "temperament": 200}


def read_pet_form(f):
    errors = []
    vals = {k: (f.get(k, "").strip() or None) for k in TEXT_FIELDS}
    for k, cap in ((k, LONG_FIELDS.get(k, 120)) for k in TEXT_FIELDS):
        if vals[k]:
            vals[k] = vals[k][:cap]
    for k, label in (("name", "Pet name"), ("species", "Species"), ("area", "Area / locality"),
                     ("city", "City")):
        if not vals[k]:
            errors.append(f"{label} is required.")
    if vals["kind"] not in ("owned", "community"):
        vals["kind"] = "owned"
    if vals["sterilized"] not in ("Yes", "No", "Unknown"):
        vals["sterilized"] = "Unknown"
    if vals["backup_phone"] and not digits_ok(vals["backup_phone"]):
        errors.append("The backup contact's phone number is not valid.")
    if not valid_date(vals["sterilized_date"] or ""):
        errors.append("Sterilization date must be a valid date.")
    for k, lo, hi in (("birth_month", 1, 12), ("birth_year", 1990, 2100)):
        if vals[k] is not None:
            try:
                n = int(vals[k])
                vals[k] = n if lo <= n <= hi else None
            except ValueError:
                vals[k] = None
    for k in PRIVACY_FIELDS:
        vals[k] = 1 if f.get(k) else 0
    rows, verrs = parse_vaccinations(f)
    return vals, rows, errors + verrs


def pet_to_form(pet):
    form = {k: ("" if pet[k] is None else str(pet[k])) for k in TEXT_FIELDS}
    form.update({k: ("on" if pet[k] else "") for k in PRIVACY_FIELDS})
    return form


def get_my_pet(pet_id):
    pet = get_db().execute("SELECT * FROM pets WHERE id = ? AND user_id = ?",
                           (pet_id, current_user()["id"])).fetchone()
    if pet is None:
        abort(404)
    return pet


def new_public_id(db):
    while True:
        pid = secrets.token_urlsafe(6)
        if not db.execute("SELECT 1 FROM pets WHERE public_id = ?", (pid,)).fetchone():
            return pid


def render_pet_form(mode, form, vacc_rows, pet=None, status=200):
    return render_template("pet_form.html", mode=mode, form=form, vacc_rows=vacc_rows, pet=pet,
                           months=MONTHS, species_list=SPECIES), status


@app.route("/pets/new", methods=["GET", "POST"])
@login_required
def pet_new():
    if current_user()["role"] == "vet":
        abort(403)
    if request.method == "GET":
        kind = "community" if request.args.get("kind") == "community" else "owned"
        return render_pet_form("new", {"kind": kind, "show_notes": "on", "show_vacc": "on",
                                       "show_map": "on"}, [])
    f = request.form
    vals, rows, errors = read_pet_form(f)
    photo = None
    if not errors:
        photo, perr = save_photo(request.files.get("photo"))
        if perr:
            errors.append(perr)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_pet_form("new", f, form_vacc_rows(f))
    db, user = get_db(), current_user()
    cols = ["public_id", "user_id", "photo", "created_at"] + TEXT_FIELDS + PRIVACY_FIELDS
    values = [new_public_id(db), user["id"], photo, now_iso()] + [vals[k] for k in TEXT_FIELDS + PRIVACY_FIELDS]
    cur = db.execute(f"INSERT INTO pets ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", values)
    save_vaccinations(db, cur.lastrowid, rows)
    db.commit()
    notify.notify_async(user["email"], [], f"PetID: {vals['name']} is registered",
                        f"Hi {user['name']},\n\n{vals['name']} is now registered on PetID.\n"
                        f"Manage the pet and download the QR tag here:\n"
                        f"{base_url()}{url_for('manage', pet_id=cur.lastrowid)}\n")
    flash("Pet registered! Download the QR tag below.", "ok")
    return redirect(url_for("manage", pet_id=cur.lastrowid))


@app.route("/pets/<int:pet_id>/edit", methods=["GET", "POST"])
@login_required
def pet_edit(pet_id):
    pet = get_my_pet(pet_id)
    if request.method == "GET":
        return render_pet_form("edit", pet_to_form(pet), vacc_rows_for_form(pet_id), pet)
    f = request.form
    vals, rows, errors = read_pet_form(f)
    photo = None
    if not errors:
        photo, perr = save_photo(request.files.get("photo"))
        if perr:
            errors.append(perr)
    if errors:
        for e in errors:
            flash(e, "error")
        return render_pet_form("edit", f, form_vacc_rows(f), pet, 200)
    db = get_db()
    sets = [f"{k} = ?" for k in TEXT_FIELDS + PRIVACY_FIELDS]
    params = [vals[k] for k in TEXT_FIELDS + PRIVACY_FIELDS]
    if photo:
        sets.append("photo = ?")
        params.append(photo)
    db.execute(f"UPDATE pets SET {', '.join(sets)} WHERE id = ?", params + [pet_id])
    save_vaccinations(db, pet_id, rows)
    db.commit()
    if photo:
        delete_photo(pet["photo"])
    flash("Saved. The QR tag did not change.", "ok")
    return redirect(url_for("manage", pet_id=pet_id))


@app.route("/pets/<int:pet_id>/delete", methods=["POST"])
@login_required
def pet_delete(pet_id):
    pet = get_my_pet(pet_id)
    db = get_db()
    db.execute("DELETE FROM pets WHERE id = ?", (pet_id,))      # children are removed automatically
    db.commit()
    delete_photo(pet["photo"])
    flash(f"{pet['name']} was deleted. Its QR tag no longer works.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/pets/<int:pet_id>")
@login_required
def manage(pet_id):
    pet = get_my_pet(pet_id)
    db = get_db()
    reports = db.execute("SELECT * FROM reports WHERE pet_id = ? ORDER BY id DESC", (pet_id,)).fetchall()
    scans = db.execute("SELECT * FROM scans WHERE pet_id = ? ORDER BY id DESC LIMIT 20", (pet_id,)).fetchall()
    scan_count = db.execute("SELECT COUNT(*) FROM scans WHERE pet_id = ?", (pet_id,)).fetchone()[0]
    pending = db.execute("SELECT * FROM transfers WHERE pet_id = ? AND status = 'pending' AND expires_at > ?",
                         (pet_id, now_iso())).fetchone()
    return render_template("manage.html", pet=pet, reports=reports, scans=scans, scan_count=scan_count,
                           pending_transfer=pending, vaccs=load_vaccinations(pet_id),
                           public_link=base_url() + url_for("pet_page", public_id=pet["public_id"]))


@app.route("/pets/<int:pet_id>/preview")
@login_required
def preview(pet_id):
    """Owner's own view of the public page. Not counted as a scan, no alerts."""
    return render_pet(get_my_pet(pet_id))


@app.route("/pets/<int:pet_id>/lost", methods=["POST"])
@login_required
def toggle_lost(pet_id):
    pet = get_my_pet(pet_id)
    db = get_db()
    db.execute("UPDATE pets SET lost = ? WHERE id = ?", (0 if pet["lost"] else 1, pet_id))
    db.commit()
    flash("Lost mode is now " + ("OFF." if pet["lost"] else "ON."), "ok")
    return redirect(url_for("manage", pet_id=pet_id))


@app.route("/pets/<int:pet_id>/rotate", methods=["POST"])
@login_required
def rotate_qr(pet_id):
    get_my_pet(pet_id)
    db = get_db()
    db.execute("UPDATE pets SET public_id = ? WHERE id = ?", (new_public_id(db), pet_id))
    db.commit()
    flash("New QR code created. The OLD printed tag no longer works - download and print the new one.", "ok")
    return redirect(url_for("manage", pet_id=pet_id))


@app.route("/pets/<int:pet_id>/reports/<int:report_id>/delete", methods=["POST"])
@login_required
def report_delete(pet_id, report_id):
    get_my_pet(pet_id)
    db = get_db()
    db.execute("DELETE FROM reports WHERE id = ? AND pet_id = ?", (report_id, pet_id))
    db.commit()
    flash("Message deleted.", "ok")
    return redirect(url_for("manage", pet_id=pet_id))


@app.route("/pets/<int:pet_id>/reports/clear", methods=["POST"])
@login_required
def reports_clear(pet_id):
    get_my_pet(pet_id)
    db = get_db()
    db.execute("DELETE FROM reports WHERE pet_id = ?", (pet_id,))
    db.commit()
    flash("All finder messages deleted.", "ok")
    return redirect(url_for("manage", pet_id=pet_id))


@app.route("/pets/<int:pet_id>/scans/clear", methods=["POST"])
@login_required
def scans_clear(pet_id):
    get_my_pet(pet_id)
    db = get_db()
    db.execute("DELETE FROM scans WHERE pet_id = ?", (pet_id,))
    db.commit()
    flash("Scan history cleared.", "ok")
    return redirect(url_for("manage", pet_id=pet_id))


# ---------- PDFs ----------
def pet_url(pet):
    return base_url() + url_for("pet_page", public_id=pet["public_id"])


@app.route("/pets/<int:pet_id>/tag.pdf")
@login_required
def download_tag(pet_id):
    pet = get_my_pet(pet_id)
    return send_file(pdfs.build_tag_pdf(pet, pet_url(pet)), mimetype="application/pdf",
                     as_attachment=True, download_name=f"petid-tag-{pet['public_id']}.pdf")


@app.route("/pets/<int:pet_id>/poster.pdf")
@login_required
def download_poster(pet_id):
    pet = get_my_pet(pet_id)
    phone = current_user()["phone"] if request.args.get("phone") == "1" else None
    return send_file(pdfs.build_poster_pdf(pet, pet_url(pet), UPLOAD_DIR, phone),
                     mimetype="application/pdf", as_attachment=True,
                     download_name=f"lost-poster-{pet['public_id']}.pdf")


@app.route("/pets/<int:pet_id>/health-card.pdf")
@login_required
def download_health_card(pet_id):
    pet = get_my_pet(pet_id)
    return send_file(pdfs.build_health_card_pdf(pet, load_vaccinations(pet_id), current_user()),
                     mimetype="application/pdf", as_attachment=True,
                     download_name=f"health-card-{pet['public_id']}.pdf")


# ---------- ownership transfer ----------
@app.route("/pets/<int:pet_id>/transfer", methods=["POST"])
@login_required
def transfer_start(pet_id):
    pet, user, db = get_my_pet(pet_id), current_user(), get_db()
    to_email = request.form.get("to_email", "").strip().lower()
    if not EMAIL_RE.match(to_email) or to_email == user["email"]:
        flash("Enter the new owner's email address (different from yours).", "error")
        return redirect(url_for("manage", pet_id=pet_id))
    db.execute("UPDATE transfers SET status = 'cancelled' WHERE pet_id = ? AND status = 'pending'", (pet_id,))
    token = secrets.token_urlsafe(24)
    db.execute("INSERT INTO transfers (pet_id, token, to_email, created_at, expires_at) VALUES (?,?,?,?,?)",
               (pet_id, token, to_email, now_iso(), (datetime.now() + timedelta(days=7)).isoformat(timespec="seconds")))
    db.commit()
    link = base_url() + url_for("transfer_accept", token=token)
    notify.notify_async(to_email, [], f"{user['name']} wants to transfer {pet['name']} to you on PetID",
                        f"Hi,\n\n{user['name']} wants to transfer {pet['name']} to your PetID account.\n"
                        f"Log in (or sign up with this email) and accept here within 7 days:\n{link}\n")
    flash(f"Transfer request sent to {to_email}. It expires in 7 days.", "ok")
    return redirect(url_for("manage", pet_id=pet_id))


@app.route("/transfer/<token>", methods=["GET", "POST"])
@login_required
def transfer_accept(token):
    db, user = get_db(), current_user()
    tr = db.execute("SELECT t.*, p.name AS pet_name, u.name AS from_name FROM transfers t "
                    "JOIN pets p ON p.id = t.pet_id JOIN users u ON u.id = p.user_id "
                    "WHERE t.token = ?", (token,)).fetchone()
    if not tr or tr["status"] != "pending" or tr["expires_at"] <= now_iso():
        abort(404)
    if tr["to_email"].lower() != user["email"]:
        flash(f"This transfer was sent to a different email address. Log in as {tr['to_email']}.", "error")
        abort(403)
    if request.method == "GET":
        return render_template("transfer_accept.html", tr=tr)
    db.execute("UPDATE pets SET user_id = ?, backup_name = NULL, backup_phone = NULL WHERE id = ?",
               (user["id"], tr["pet_id"]))
    db.execute("UPDATE transfers SET status = 'accepted' WHERE id = ?", (tr["id"],))
    db.commit()
    flash(f"{tr['pet_name']} is now in your account. The existing QR tag keeps working.", "ok")
    return redirect(url_for("manage", pet_id=tr["pet_id"]))


# =====================================================================
#  Vets
# =====================================================================
def vet_ok():
    u = current_user()
    return u is not None and u["role"] == "vet" and u["vet_approved"]


@app.route("/vet", methods=["GET", "POST"])
@login_required
def vet_home():
    user = current_user()
    if user["role"] != "vet":
        abort(403)
    if not user["vet_approved"]:
        return render_template("vet_pending.html")
    if request.method == "POST":
        code = request.form.get("code", "").strip().rstrip("/").split("/")[-1]
        pet = get_db().execute("SELECT public_id FROM pets WHERE public_id = ?", (code,)).fetchone()
        if not pet:
            flash("No pet found with that code. Use the code printed on the tag URL (the part after /p/).", "error")
        else:
            return redirect(url_for("vet_pet", public_id=pet["public_id"]))
    return render_template("vet_lookup.html")


@app.route("/vet/pet/<public_id>")
@login_required
def vet_pet(public_id):
    if not vet_ok():
        abort(403)
    pet = get_pet_public(public_id)
    return render_template("vet_pet.html", pet=pet, vaccs=load_vaccinations(pet["id"]),
                           age=age_text(pet["birth_month"], pet["birth_year"]))


@app.route("/vet/verify/<int:vacc_id>", methods=["POST"])
@login_required
def vet_verify(vacc_id):
    if not vet_ok():
        abort(403)
    db, vet = get_db(), current_user()
    row = db.execute("SELECT v.id, p.public_id FROM vaccinations v JOIN pets p ON p.id = v.pet_id "
                     "WHERE v.id = ?", (vacc_id,)).fetchone()
    if not row:
        abort(404)
    db.execute("UPDATE vaccinations SET verified_by = ?, verified_at = ?, verified_user_id = ? WHERE id = ?",
               (f"{vet['clinic_name']} (Dr. {vet['name']})", now_iso(), vet["id"], vacc_id))
    db.commit()
    flash("Vaccination marked as verified.", "ok")
    return redirect(url_for("vet_pet", public_id=row["public_id"]))


# =====================================================================
#  Admin
# =====================================================================
@app.route("/admin")
@admin_required
def admin():
    db = get_db()
    one = lambda sql, *p: db.execute(sql, p).fetchone()[0]          # noqa: E731
    week_ago = (datetime.now() - timedelta(days=7)).isoformat(timespec="seconds")
    today, soon = date.today().isoformat(), (date.today() + timedelta(days=14)).isoformat()
    stats = {
        "Owners": one("SELECT COUNT(*) FROM users WHERE role = 'owner'"),
        "Vets": one("SELECT COUNT(*) FROM users WHERE role = 'vet'"),
        "Pets": one("SELECT COUNT(*) FROM pets WHERE kind = 'owned'"),
        "Community animals": one("SELECT COUNT(*) FROM pets WHERE kind = 'community'"),
        "Scans (all time)": one("SELECT COUNT(*) FROM scans"),
        "Scans (last 7 days)": one("SELECT COUNT(*) FROM scans WHERE scanned_at >= ?", week_ago),
        "Finder messages": one("SELECT COUNT(*) FROM reports"),
        "Currently lost": one("SELECT COUNT(*) FROM pets WHERE lost = 1"),
    }
    lost = db.execute("SELECT p.name, p.city, u.email FROM pets p JOIN users u ON u.id = p.user_id "
                      "WHERE p.lost = 1 ORDER BY p.id DESC LIMIT 50").fetchall()
    due = db.execute("SELECT p.name, v.vaccine_name, v.next_due, u.email FROM vaccinations v "
                     "JOIN pets p ON p.id = v.pet_id JOIN users u ON u.id = p.user_id "
                     "WHERE v.next_due BETWEEN ? AND ? ORDER BY v.next_due LIMIT 50", (today, soon)).fetchall()
    vets = db.execute("SELECT * FROM users WHERE role = 'vet' ORDER BY vet_approved, id DESC").fetchall()
    recent = db.execute("SELECT name, email, role, created_at FROM users ORDER BY id DESC LIMIT 10").fetchall()
    return render_template("admin.html", stats=stats, lost=lost, due=due, vets=vets, recent=recent)


@app.route("/admin/vet/<int:user_id>/<action>", methods=["POST"])
@admin_required
def admin_vet(user_id, action):
    if action not in ("approve", "revoke"):
        abort(404)
    db = get_db()
    db.execute("UPDATE users SET vet_approved = ? WHERE id = ? AND role = 'vet'",
               (1 if action == "approve" else 0, user_id))
    db.commit()
    flash("Vet " + ("approved." if action == "approve" else "access revoked."), "ok")
    return redirect(url_for("admin"))


# =====================================================================
#  Scheduled jobs
# =====================================================================
@app.route("/tasks/reminders")
def tasks_reminders():
    """Call this once a day from a free cron service: /tasks/reminders?token=<CRON_TOKEN>"""
    token = os.environ.get("CRON_TOKEN", "")
    if not token or not safe_equal(request.args.get("token", ""), token):
        abort(403)
    return jsonify(sent=tasks.run_reminders(get_db()))


init_db()
if os.environ.get("ENABLE_SCHEDULER") == "1":       # for hosts running a single worker
    tasks.start_scheduler()

if __name__ == "__main__":
    if os.environ.get("RUN_SCHEDULER", "1") == "1" and os.environ.get("ENABLE_SCHEDULER") != "1":
        tasks.start_scheduler()
    # host 0.0.0.0 lets a phone on the same Wi-Fi open the site (needed to test QR scanning)
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")),
            debug=os.environ.get("DEBUG") == "1")
