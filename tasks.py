"""Background jobs: vaccination reminders (email + SMS)."""
import os
import sqlite3
import threading
import time
from datetime import date, datetime

import notify
from db import connect_standalone


def run_reminders(conn, today=None):
    """Send 'due within a week' and 'overdue' reminders. Each reminder is sent only once
    (recorded in reminders_sent), so it is safe to run this as often as you like."""
    today = today or date.today()
    base = os.environ.get("BASE_URL", "").rstrip("/")
    rows = conn.execute(
        """SELECT v.id AS vid, v.vaccine_name, v.next_due, p.name AS pet_name,
                  u.name AS owner_name, u.email, u.phone, p.backup_phone
           FROM vaccinations v JOIN pets p ON p.id = v.pet_id JOIN users u ON u.id = p.user_id
           WHERE v.next_due IS NOT NULL AND v.next_due != ''""").fetchall()
    sent = 0
    for r in rows:
        try:
            due = date.fromisoformat(r["next_due"])
        except ValueError:
            continue
        days = (due - today).days
        if 0 <= days <= 7:
            kind = "week"
        elif -30 <= days < 0:
            kind = "overdue"
        else:
            continue
        try:  # record first: guarantees at most one reminder per vaccination & due date
            conn.execute("INSERT INTO reminders_sent (vaccination_id, kind, due_date, sent_at) "
                         "VALUES (?,?,?,?)", (r["vid"], kind, r["next_due"],
                                              datetime.now().isoformat(timespec="seconds")))
            conn.commit()
        except sqlite3.IntegrityError:
            continue
        when = due.strftime("%d %b %Y")
        if kind == "week":
            subject = f"PetID reminder: {r['pet_name']}'s {r['vaccine_name']} is due soon"
            line = f"is due on {when} ({'today' if days == 0 else f'in {days} day' + ('s' if days != 1 else '')})."
            sms = f"PetID: {r['pet_name']}'s {r['vaccine_name']} vaccine is due on {when}."
        else:
            subject = f"PetID: {r['pet_name']}'s {r['vaccine_name']} is overdue"
            line = f"was due on {when} and is now {-days} day{'s' if -days != 1 else ''} overdue."
            sms = f"PetID: {r['pet_name']}'s {r['vaccine_name']} vaccine was due on {when}. Please book it."
        body = (f"Hi {r['owner_name']},\n\n{r['pet_name']}'s {r['vaccine_name']} vaccination {line}\n\n"
                f"After the visit, update the record here: {base}/dashboard\n")
        phones = [r["phone"]] + ([r["backup_phone"]] if r["backup_phone"] else [])
        notify.notify_async(r["email"], phones, subject, body, sms)
        sent += 1
    return sent


def start_scheduler(every_seconds=6 * 3600):
    """Check for due vaccinations every few hours while the app is running."""
    def loop():
        while True:
            try:
                conn = connect_standalone()
                try:
                    n = run_reminders(conn)
                    if n:
                        print(f"[PetID] sent {n} vaccination reminder(s)")
                finally:
                    conn.close()
            except Exception as exc:
                print("[PetID] reminder job failed:", exc)
            time.sleep(every_seconds)
    threading.Thread(target=loop, daemon=True).start()
