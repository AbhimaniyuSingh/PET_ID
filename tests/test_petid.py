"""Automated tests for PetID.   Run from the project folder:   python -m unittest -v
(Uses a temporary database; nothing here touches your real petid.db.)"""
import io
import json
import os
import re
import sqlite3
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

TMP = tempfile.mkdtemp(prefix="petid-test-")
os.environ.update(PETID_DATA_DIR=TMP, SCAN_ALERT_COOLDOWN="60", CRON_TOKEN="cron-secret",
                  ADMIN_EMAIL="admin@example.com", BASE_URL="http://testserver")
for var in ("SMTP_USER", "SMTP_PASSWORD", "SMS_PROVIDER"):
    os.environ.pop(var, None)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as petid          # noqa: E402
import db as dbmod           # noqa: E402
import notify                # noqa: E402
import security              # noqa: E402
from PIL import Image        # noqa: E402

DB = os.path.join(TMP, "petid.db")
FORM_RE = re.compile(r"<form\b[^>]*method=\"post\"[^>]*>(.*?)</form>", re.S | re.I)


def sql(query, *params):
    conn = sqlite3.connect(DB)
    try:
        rows = conn.execute(query, params).fetchall()
        conn.commit()
        return rows
    finally:
        conn.close()


def days(n):
    return (date.today() + timedelta(days=n)).isoformat()


def pet_data(**over):
    d = dict(kind="owned", name="Bruno", species="Dog", breed="Labrador", gender="Male", color="Brown",
             birth_month="3", birth_year="2022", area="Gandhi Nagar", city="Jammu", show_vacc="on",
             show_notes="on", show_map="on", vacc_count="0")
    d.update(over)
    return d


def png():
    buf = io.BytesIO()
    Image.new("RGB", (40, 40), "red").save(buf, "PNG")
    buf.seek(0)
    return buf


class Client:
    def __init__(self):
        self.c = petid.app.test_client()

    def get(self, url, **kw):
        return self.c.get(url, **kw)

    def html(self, url):
        return self.c.get(url).get_data(as_text=True)

    def token(self):
        return re.search(r'name="csrf_token" value="([^"]+)"', self.html("/login")).group(1)

    def captcha(self):
        with self.c.session_transaction() as s:
            return str(s["captcha"])

    def post(self, url, data=None, token=True, **kw):
        data = dict(data or {})
        if token:
            data.setdefault("csrf_token", self.token())
        return self.c.post(url, data=data, **kw)

    def signup(self, email, role="owner", **extra):
        self.get("/signup")
        data = dict(name="Asha Kumar", email=email, phone="9876543210", password="password123",
                    role=role, captcha=self.captcha())
        data.update(extra)
        return self.post("/signup", data)

    def login(self, email, password="password123", **extra):
        return self.post("/login", dict(email=email, password=password, **extra))

    def make_pet(self, **over):
        r = self.post("/pets/new", pet_data(**over))
        assert r.status_code == 302, r.get_data(as_text=True)[:500]
        return int(r.headers["Location"].rsplit("/", 1)[1])


class Base(unittest.TestCase):
    def setUp(self):
        for table in ("reminders_sent", "transfers", "reports", "scans", "vaccinations", "pets", "users"):
            sql(f"DELETE FROM {table}")
        security._hits.clear()
        os.environ["SCAN_ALERT_COOLDOWN"] = "60"
        self.sent = []
        patcher = mock.patch.object(
            notify, "notify_async",
            lambda email, phones, subject, body, sms=None: self.sent.append(
                dict(email=email, phones=phones, subject=subject, body=body, sms=sms)))
        patcher.start()
        self.addCleanup(patcher.stop)

    def owner(self, email="owner@example.com", **extra):
        c = Client()
        r = c.signup(email, **extra)
        self.assertEqual(r.status_code, 302, r.get_data(as_text=True)[:400])
        return c

    def public_id(self, pet_id):
        return sql("SELECT public_id FROM pets WHERE id = ?", pet_id)[0][0]


class TestAccounts(Base):
    def test_signup_login_logout(self):
        c = self.owner()
        self.assertEqual(c.get("/dashboard").status_code, 200)
        c.post("/logout")
        self.assertEqual(c.get("/dashboard").status_code, 302)       # sent to login
        self.assertEqual(c.login("owner@example.com", "wrong-password").status_code, 401)
        r = c.login("owner@example.com")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/dashboard"))

    def test_signup_validation(self):
        c = Client()
        c.get("/signup")
        bad_captcha = dict(name="A", email="a@example.com", phone="9876543210", password="password123",
                           role="owner", captcha="999")
        self.assertEqual(c.post("/signup", bad_captcha).status_code, 400)
        self.assertEqual(c.signup("a@example.com", password="short").status_code, 400)
        self.assertEqual(c.signup("not-an-email").status_code, 400)
        self.assertEqual(c.signup("a@example.com", phone="123").status_code, 400)
        self.assertEqual(c.signup("a@example.com", website="http://spam").status_code, 400)  # honeypot
        self.assertEqual(c.signup("a@example.com", role="vet").status_code, 400)             # needs clinic
        self.assertEqual(sql("SELECT COUNT(*) FROM users")[0][0], 0)
        self.assertEqual(Client().signup("dup@example.com").status_code, 302)
        self.assertEqual(Client().signup("DUP@example.com").status_code, 400)               # duplicate

    def test_csrf_required(self):
        c = Client()
        r = c.post("/login", dict(email="x@example.com", password="y"), token=False)
        self.assertEqual(r.status_code, 400)

    def test_login_redirect_is_safe(self):
        self.owner().post("/logout")
        c = Client()
        c.signup("b@example.com")
        c.post("/logout")
        r = c.login("b@example.com", next="//evil.com")
        self.assertTrue(r.headers["Location"].endswith("/dashboard"))
        c.post("/logout")
        r = c.login("b@example.com", next="/account")
        self.assertTrue(r.headers["Location"].endswith("/account"))

    def test_login_rate_limit(self):
        c = self.owner()
        c.post("/logout")
        codes = [c.login("owner@example.com", "nope").status_code for _ in range(12)]
        self.assertIn(429, codes)

    def test_password_reset_flow(self):
        c = self.owner()
        c.post("/logout")
        self.sent.clear()
        c.post("/forgot", dict(email="owner@example.com"))
        self.assertEqual(len(self.sent), 1)
        token = re.search(r"/reset/(\S+)", self.sent[0]["body"]).group(1)
        self.assertEqual(c.get(f"/reset/{token}").status_code, 200)
        self.assertEqual(c.post(f"/reset/{token}", dict(password="short")).status_code, 400)
        self.assertEqual(c.post(f"/reset/{token}", dict(password="brand-new-pass")).status_code, 302)
        self.assertEqual(c.login("owner@example.com", "brand-new-pass").status_code, 302)
        self.assertEqual(c.get(f"/reset/{token}").status_code, 302)   # link can't be reused
        self.sent.clear()
        c.post("/forgot", dict(email="nobody@example.com"))           # unknown email: no mail, same reply
        self.assertEqual(self.sent, [])

    def test_account_update(self):
        c = self.owner()
        c.post("/account", dict(action="profile", name="New Name", phone="9000011111"))
        self.assertEqual(sql("SELECT name, phone FROM users")[0], ("New Name", "9000011111"))
        c.post("/account", dict(action="password", current="password123", new="another-pass-1"))
        c.post("/logout")
        self.assertEqual(c.login("owner@example.com", "another-pass-1").status_code, 302)


class TestPets(Base):
    def test_create_edit_delete_with_photo(self):
        c = self.owner()
        pet_id = c.make_pet(photo=(png(), "dog.png"), vacc_count="1", vacc_name_1="Anti-rabies",
                            vacc_date_1=days(-30), vacc_next_1=days(335), microchip="MC123",
                            backup_name="Ravi", backup_phone="9000000001")
        photo = sql("SELECT photo FROM pets WHERE id = ?", pet_id)[0][0]
        self.assertTrue(os.path.exists(os.path.join(TMP, "uploads", photo)))
        self.assertIn("Bruno", c.html(f"/pets/{pet_id}"))
        self.assertIn("Anti-rabies", c.html(f"/pets/{pet_id}/edit"))
        c.post(f"/pets/{pet_id}/edit", pet_data(name="Bruno II", vacc_count="2", vacc_name_1="Anti-rabies",
               vacc_date_1=days(-30), vacc_name_2="DHPPi", vacc_date_2=days(-10)))
        self.assertEqual(sql("SELECT name FROM pets")[0][0], "Bruno II")
        self.assertEqual(sql("SELECT COUNT(*) FROM vaccinations")[0][0], 2)
        pid = self.public_id(pet_id)
        c.post(f"/pets/{pet_id}/delete")
        self.assertEqual(Client().get(f"/p/{pid}").status_code, 404)
        self.assertEqual(sql("SELECT COUNT(*) FROM vaccinations")[0][0], 0)
        self.assertFalse(os.path.exists(os.path.join(TMP, "uploads", photo)))

    def test_validation_errors(self):
        c = self.owner()
        self.assertEqual(c.post("/pets/new", pet_data(name="")).status_code, 200)
        self.assertEqual(c.post("/pets/new", pet_data(city="")).status_code, 200)
        self.assertEqual(c.post("/pets/new", pet_data(vacc_count="1", vacc_name_1="")).status_code, 200)
        self.assertEqual(c.post("/pets/new", pet_data(backup_phone="12")).status_code, 200)
        self.assertEqual(c.post("/pets/new", pet_data(photo=(io.BytesIO(b"not an image"), "x.png"))).status_code, 200)
        self.assertEqual(sql("SELECT COUNT(*) FROM pets")[0][0], 0)

    def test_other_users_cannot_touch_my_pet(self):
        a, b = self.owner("a@example.com"), self.owner("b@example.com")
        pet_id = a.make_pet()
        for url in (f"/pets/{pet_id}", f"/pets/{pet_id}/edit", f"/pets/{pet_id}/tag.pdf",
                    f"/pets/{pet_id}/poster.pdf", f"/pets/{pet_id}/health-card.pdf", f"/pets/{pet_id}/preview"):
            self.assertEqual(b.get(url).status_code, 404, url)
        for url in (f"/pets/{pet_id}/delete", f"/pets/{pet_id}/lost", f"/pets/{pet_id}/rotate"):
            self.assertEqual(b.post(url).status_code, 404, url)
        self.assertEqual(Client().get(f"/pets/{pet_id}").status_code, 302)   # logged-out -> login

    def test_rotate_qr_invalidates_old_tag(self):
        c = self.owner()
        pet_id = c.make_pet()
        old = self.public_id(pet_id)
        c.post(f"/pets/{pet_id}/rotate")
        new = self.public_id(pet_id)
        self.assertNotEqual(old, new)
        self.assertEqual(Client().get(f"/p/{old}").status_code, 404)
        self.assertEqual(Client().get(f"/p/{new}").status_code, 200)

    def test_lost_mode_banner(self):
        c = self.owner()
        pet_id = c.make_pet()
        pid = self.public_id(pet_id)
        c.post(f"/pets/{pet_id}/lost")
        self.assertIn("lost-banner", Client().get(f"/p/{pid}").get_data(as_text=True))
        c.post(f"/pets/{pet_id}/lost")
        self.assertNotIn("lost-banner", Client().get(f"/p/{pid}").get_data(as_text=True))

    def test_per_field_privacy(self):
        c = self.owner()
        pet_id = c.make_pet(microchip="MC987654", medical_notes="Allergic to chicken", show_notes="",
                            show_vacc="", show_map="", vacc_count="1", vacc_name_1="SecretVaccine")
        html = Client().get(f"/p/{self.public_id(pet_id)}").get_data(as_text=True)
        for hidden in ("MC987654", "Allergic to chicken", "SecretVaccine", "maps.google.com/maps?q", "9876543210"):
            self.assertNotIn(hidden, html)
        c.post(f"/pets/{pet_id}/edit", pet_data(microchip="MC987654", medical_notes="Allergic to chicken",
               show_microchip="on", show_phone="on", vacc_count="1", vacc_name_1="SecretVaccine"))
        html = Client().get(f"/p/{self.public_id(pet_id)}").get_data(as_text=True)
        for shown in ("MC987654", "Allergic to chicken", "SecretVaccine", "maps.google.com/maps?q", "9876543210"):
            self.assertIn(shown, html)


class TestScansAndReports(Base):
    def test_scan_alerts_email_sms_backup_and_cooldown(self):
        c = self.owner()
        pet_id = c.make_pet(backup_phone="9000000001")
        pid = self.public_id(pet_id)
        self.sent.clear()
        finder = Client()
        finder.get(f"/p/{pid}")
        self.assertEqual(len(self.sent), 1)
        n = self.sent[0]
        self.assertEqual(n["email"], "owner@example.com")
        self.assertEqual(n["phones"], ["9876543210", "9000000001"])
        self.assertIn("scanned", n["sms"])
        finder.get(f"/p/{pid}")                                    # inside the cooldown
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(sql("SELECT COUNT(*) FROM scans")[0][0], 2)
        os.environ["SCAN_ALERT_COOLDOWN"] = "0"
        finder.get(f"/p/{pid}")
        finder.get(f"/p/{pid}")
        self.assertEqual(len(self.sent), 3)
        c.get(f"/pets/{pet_id}/preview")                           # owner preview is not a scan
        self.assertEqual(len(self.sent), 3)
        self.assertEqual(sql("SELECT COUNT(*) FROM scans")[0][0], 4)

    def test_finder_report_with_gps_and_captcha(self):
        c = self.owner()
        pet_id = c.make_pet()
        pid = self.public_id(pet_id)
        f = Client()
        f.get(f"/p/{pid}")
        self.sent.clear()
        f.post(f"/p/{pid}/report", dict(captcha="0", message="found him"))            # wrong captcha
        self.assertEqual(sql("SELECT COUNT(*) FROM reports")[0][0], 0)
        f.get(f"/p/{pid}")
        f.post(f"/p/{pid}/report", dict(captcha=f.captcha(), message="found him", website="spam"))
        self.assertEqual(sql("SELECT COUNT(*) FROM reports")[0][0], 0)               # honeypot
        f.get(f"/p/{pid}")
        self.sent.clear()
        f.post(f"/p/{pid}/report", dict(captcha=f.captcha(), finder_name="Ravi", finder_phone="9111111111",
               message="near the park", lat="32.7266", lng="74.8570"))
        self.assertEqual(sql("SELECT lat, lng FROM reports")[0], (32.7266, 74.857))
        self.assertEqual(len(self.sent), 1)
        self.assertIn("google.com/maps?q=32.7266,74.857", self.sent[0]["body"])
        self.assertIn("9111111111", self.sent[0]["sms"])
        f.get(f"/p/{pid}")
        f.post(f"/p/{pid}/report", dict(captcha=f.captcha(), message="x", lat="999", lng="74"))
        self.assertIsNone(sql("SELECT lat FROM reports ORDER BY id DESC")[0][0])     # bad GPS ignored
        self.assertIn("near the park", c.html(f"/pets/{pet_id}"))

    def test_rate_limiter(self):
        results = [security.rate_limited("k", 3, 60) for _ in range(5)]
        self.assertEqual(results, [False, False, False, True, True])


class TestReminders(Base):
    def test_reminders_sent_once(self):
        c = self.owner()
        c.make_pet(vacc_count="3", vacc_name_1="Soon", vacc_next_1=days(3), vacc_name_2="Late",
                   vacc_next_2=days(-5), vacc_name_3="Far", vacc_next_3=days(60))
        self.sent.clear()
        self.assertEqual(Client().get("/tasks/reminders").status_code, 403)
        self.assertEqual(Client().get("/tasks/reminders?token=wrong").status_code, 403)
        r = Client().get("/tasks/reminders?token=cron-secret")
        self.assertEqual(json.loads(r.get_data())["sent"], 2)
        subjects = " | ".join(n["subject"] for n in self.sent)
        self.assertIn("due soon", subjects)
        self.assertIn("overdue", subjects)
        self.assertTrue(all(n["sms"] and n["phones"] for n in self.sent))
        self.assertEqual(json.loads(Client().get("/tasks/reminders?token=cron-secret").get_data())["sent"], 0)


class TestVets(Base):
    def test_vet_verification_flow(self):
        owner = self.owner()
        pet_id = owner.make_pet(vacc_count="1", vacc_name_1="Anti-rabies", vacc_date_1=days(-20),
                                vacc_next_1=days(345))
        pid = self.public_id(pet_id)
        vet = Client()
        r = vet.signup("vet@example.com", role="vet", clinic_name="Happy Paws Clinic", vet_reg_no="J&K-1234")
        self.assertEqual(r.status_code, 302)
        self.assertIn("Waiting for approval", vet.html("/vet"))
        self.assertEqual(vet.get(f"/vet/pet/{pid}").status_code, 403)
        admin = self.owner("admin@example.com")
        self.assertEqual(sql("SELECT role FROM users WHERE email = 'admin@example.com'")[0][0], "admin")
        self.assertIn("Happy Paws Clinic", admin.html("/admin"))
        vet_id = sql("SELECT id FROM users WHERE email = 'vet@example.com'")[0][0]
        admin.post(f"/admin/vet/{vet_id}/approve")
        self.assertIn("Find pet", vet.html("/vet"))
        self.assertEqual(vet.post("/vet", dict(code="zzzz")).status_code, 200)
        r = vet.post("/vet", dict(code=f"http://testserver/p/{pid}"))
        self.assertTrue(r.headers["Location"].endswith(f"/vet/pet/{pid}"))
        self.assertIn("Verify this vaccination", vet.html(f"/vet/pet/{pid}"))
        vacc_id = sql("SELECT id FROM vaccinations")[0][0]
        self.assertEqual(owner.post(f"/vet/verify/{vacc_id}").status_code, 403)       # owners can't verify
        vet.post(f"/vet/verify/{vacc_id}")
        self.assertIn("Happy Paws Clinic", Client().get(f"/p/{pid}").get_data(as_text=True))
        # editing only the next-due date keeps the tick; changing the name or date removes it
        owner.post(f"/pets/{pet_id}/edit", pet_data(vacc_count="1", vacc_name_1="Anti-rabies",
                   vacc_date_1=days(-20), vacc_next_1=days(300)))
        self.assertIsNotNone(sql("SELECT verified_by FROM vaccinations")[0][0])
        owner.post(f"/pets/{pet_id}/edit", pet_data(vacc_count="1", vacc_name_1="Anti-rabies",
                   vacc_date_1=days(-5), vacc_next_1=days(300)))
        self.assertIsNone(sql("SELECT verified_by FROM vaccinations")[0][0])

    def test_admin_only(self):
        c = self.owner()
        self.assertEqual(c.get("/admin").status_code, 403)
        self.assertEqual(Client().get("/admin").status_code, 302)
        admin = self.owner("admin@example.com")
        self.assertIn("Admin dashboard", admin.html("/admin"))


class TestTransfer(Base):
    def test_transfer_between_users(self):
        a, b, stranger = self.owner("a@example.com"), self.owner("b@example.com"), self.owner("c@example.com")
        pet_id = a.make_pet()
        self.sent.clear()
        a.post(f"/pets/{pet_id}/transfer", dict(to_email="a@example.com"))      # can't transfer to self
        self.assertEqual(self.sent, [])
        a.post(f"/pets/{pet_id}/transfer", dict(to_email="b@example.com"))
        token = re.search(r"/transfer/(\S+)", self.sent[0]["body"]).group(1)
        self.assertIn("wants to transfer", b.html("/dashboard"))
        self.assertEqual(stranger.get(f"/transfer/{token}").status_code, 403)
        self.assertEqual(b.get(f"/transfer/{token}").status_code, 200)
        b.post(f"/transfer/{token}")
        self.assertEqual(b.get(f"/pets/{pet_id}").status_code, 200)
        self.assertEqual(a.get(f"/pets/{pet_id}").status_code, 404)
        self.assertEqual(b.get(f"/transfer/{token}").status_code, 404)          # already used
        self.sent.clear()
        Client().get(f"/p/{self.public_id(pet_id)}")
        self.assertEqual(self.sent[0]["email"], "b@example.com")                # alerts go to the new owner


class TestCommunityPdfLangPwa(Base):
    def test_community_listing(self):
        c = self.owner()
        c.make_pet(kind="community", name="Tommy", area="Old City", city="Jammu", caretaker_note="Fed by tea stall")
        c.make_pet(name="PrivatePet", area="Somewhere", city="Delhi")
        html = Client().get("/community").get_data(as_text=True)
        self.assertIn("Tommy", html)
        self.assertIn("Old City", html)
        self.assertNotIn("PrivatePet", html)
        self.assertIn("Tommy", Client().get("/community?city=jammu").get_data(as_text=True))
        self.assertNotIn("Tommy", Client().get("/community?city=Delhi").get_data(as_text=True))

    def test_pdfs(self):
        c = self.owner()
        pet_id = c.make_pet(photo=(png(), "dog.png"), vacc_count="1", vacc_name_1="Anti-rabies")
        for url in (f"/pets/{pet_id}/tag.pdf", f"/pets/{pet_id}/poster.pdf",
                    f"/pets/{pet_id}/poster.pdf?phone=1", f"/pets/{pet_id}/health-card.pdf"):
            r = c.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertEqual(r.data[:4], b"%PDF", url)
        # non-Latin names must not crash PDF generation
        pet2 = c.make_pet(name="टॉमी", area="जम्मू", city="Jammu")
        for url in (f"/pets/{pet2}/tag.pdf", f"/pets/{pet2}/poster.pdf", f"/pets/{pet2}/health-card.pdf"):
            self.assertEqual(c.get(url).status_code, 200, url)

    def test_language_toggle(self):
        c = self.owner()
        pid = self.public_id(c.make_pet())
        f = Client()
        self.assertIn("Send to owner", f.html(f"/p/{pid}"))
        r = f.get("/lang/hi", headers={"Referer": f"http://testserver/p/{pid}"})
        self.assertTrue(r.headers["Location"].endswith(f"/p/{pid}"))
        self.assertIn("मालिक को भेजें", f.html(f"/p/{pid}"))
        f.get("/lang/en")
        self.assertIn("Send to owner", f.html(f"/p/{pid}"))
        auto = Client().get(f"/p/{pid}", headers={"Accept-Language": "hi-IN,hi;q=0.9"}).get_data(as_text=True)
        self.assertIn("मालिक को भेजें", auto)

    def test_pwa_files(self):
        c = Client()
        r = c.get("/manifest.webmanifest")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(json.loads(r.get_data())["short_name"], "PetID")
        r = c.get("/sw.js")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["Service-Worker-Allowed"], "/")

    def test_security_headers(self):
        r = Client().get("/")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")


class TestEveryFormHasCsrfToken(Base):
    def test_all_post_forms(self):
        owner = self.owner()
        pet_id = owner.make_pet(vacc_count="1", vacc_name_1="Anti-rabies")
        pid = self.public_id(pet_id)
        vet = Client()
        vet.signup("vet@example.com", role="vet", clinic_name="Clinic", vet_reg_no="1")
        admin = self.owner("admin@example.com")
        admin.post(f"/admin/vet/{sql('SELECT id FROM users WHERE role = ?', 'vet')[0][0]}/approve")
        pages = [(Client(), p) for p in ("/", "/login", "/signup", "/forgot", f"/p/{pid}", "/community")]
        pages += [(owner, p) for p in ("/dashboard", "/account", "/pets/new", f"/pets/{pet_id}", f"/pets/{pet_id}/edit")]
        pages += [(admin, "/admin"), (vet, "/vet"), (vet, f"/vet/pet/{pid}")]
        checked = 0
        for client, path in pages:
            html = client.html(path)
            for body in FORM_RE.findall(html):
                checked += 1
                self.assertIn('name="csrf_token"', body, f"form without CSRF token on {path}")
        self.assertGreater(checked, 12)


class TestDatabaseUpgrade(unittest.TestCase):
    def test_old_database_is_backed_up_not_lost(self):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "petid.db")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE pets (id INTEGER PRIMARY KEY, name TEXT, owner_phone TEXT)")
        conn.execute("INSERT INTO pets (name, owner_phone) VALUES ('Old', '1')")
        conn.commit()
        conn.close()
        with mock.patch.object(dbmod, "DB_PATH", path):
            dbmod.init_db()
        self.assertEqual(len([f for f in os.listdir(folder) if f.startswith("petid_old_")]), 1)
        tables = [r[0] for r in sqlite3.connect(path).execute("SELECT name FROM sqlite_master WHERE type='table'")]
        self.assertIn("users", tables)


if __name__ == "__main__":
    unittest.main()
