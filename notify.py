"""Email, SMS and WhatsApp sending. If a service isn't configured the message is printed
in the terminal instead, so the site keeps working while you develop."""
import base64
import json
import os
import smtplib
import threading
from email.message import EmailMessage
from urllib import parse
from urllib import request as urlrequest


def send_email(to, subject, body):
    user, pw = os.environ.get("SMTP_USER"), os.environ.get("SMTP_PASSWORD")
    if not (user and pw):
        print(f"\n[EMAIL not configured - would send to {to}]\nSubject: {subject}\n{body}\n")
        return
    msg = EmailMessage()
    msg["From"] = os.environ.get("MAIL_FROM", user)
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    port = int(os.environ.get("SMTP_PORT", "465"))
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.starttls()
        with server:
            server.login(user, pw)
            server.send_message(msg)
    except Exception as exc:  # never crash the website because an email failed
        print("[EMAIL failed]", exc)


def normalize_phone(raw):
    digits = "".join(ch for ch in raw if ch.isdigit())
    return "+91" + digits if len(digits) == 10 else "+" + digits  # 10 digits = Indian number


def _twilio_send(to, frm, text):
    sid, token = os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"]
    data = parse.urlencode({"To": to, "From": frm, "Body": text}).encode()
    req = urlrequest.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json", data=data)
    req.add_header("Authorization",
                   "Basic " + base64.b64encode(f"{sid}:{token}".encode()).decode())
    urlrequest.urlopen(req, timeout=15)


def send_sms(raw_phone, text):
    provider = os.environ.get("SMS_PROVIDER", "").lower()
    try:
        if provider == "twilio":
            _twilio_send(normalize_phone(raw_phone), os.environ["TWILIO_FROM"], text)
            wa_from = os.environ.get("WHATSAPP_FROM")       # e.g. whatsapp:+14155238886
            if wa_from:
                _twilio_send("whatsapp:" + normalize_phone(raw_phone), wa_from, text)
        elif provider == "fast2sms":
            digits = "".join(ch for ch in raw_phone if ch.isdigit())[-10:]
            payload = json.dumps({"route": "q", "message": text, "numbers": digits,
                                  "flash": 0}).encode()
            req = urlrequest.Request("https://www.fast2sms.com/dev/bulkV2", data=payload, headers={
                "authorization": os.environ["FAST2SMS_API_KEY"],
                "Content-Type": "application/json"})
            urlrequest.urlopen(req, timeout=15)
        else:
            print(f"\n[SMS not configured - would send to {raw_phone}]\n{text}\n")
    except Exception as exc:
        print("[SMS failed]", exc)


def notify_async(email, phones, subject, body, sms_text=None):
    """Send in a background thread so pages load instantly.
    `phones` is a list of numbers to text (owner + optional backup contact)."""
    def work():
        if email:
            send_email(email, subject, body)
        if sms_text:
            for phone in phones or []:
                send_sms(phone, sms_text)
    threading.Thread(target=work, daemon=True).start()
