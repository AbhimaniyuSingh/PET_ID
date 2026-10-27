"""Login sessions, CSRF protection, CAPTCHA and rate limiting."""
import hmac
import secrets
import time
from collections import defaultdict, deque
from functools import wraps

from flask import abort, flash, g, redirect, request, session, url_for

from db import get_db


# ---------- current user ----------
def current_user():
    if "user" not in g:
        g.user = None
        uid = session.get("uid")
        if uid:
            g.user = get_db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    return g.user


def login_user(user_id):
    session.clear()                      # new session on login (prevents session fixation)
    session["uid"] = user_id
    session.permanent = True


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            flash("Please log in to continue.", "error")
            return redirect(url_for("login", next=request.full_path.rstrip("?")))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    @login_required
    def wrapped(*args, **kwargs):
        if current_user()["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def safe_next(url):
    """Only allow redirects to pages on this site."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url:
        return url
    return None


def safe_equal(a, b):
    return hmac.compare_digest((a or "").encode(), (b or "").encode())


# ---------- CSRF ----------
def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


def check_csrf():
    if request.method == "POST":
        sent = request.form.get("csrf_token", "")
        if not sent or not safe_equal(sent, session.get("csrf", "")):
            abort(400, "Your security token is missing or expired. Please go back, "
                       "refresh the page and try again.")


# ---------- simple CAPTCHA + honeypot ----------
def captcha_question():
    a, b = secrets.randbelow(9) + 1, secrets.randbelow(9) + 1
    session["captcha"] = a + b
    return f"{a} + {b}"


def captcha_ok(form):
    expected = session.pop("captcha", None)
    try:
        return expected is not None and int(form.get("captcha", "").strip()) == expected
    except ValueError:
        return False


def honeypot_tripped(form):
    return bool(form.get("website", "").strip())   # real people never see this hidden field


# ---------- rate limiting (in memory: fine for one server process) ----------
_hits = defaultdict(deque)


def rate_limited(key, limit, window_seconds):
    """Returns True if `key` already used up `limit` actions in the last `window_seconds`."""
    now = time.time()
    q = _hits[key]
    while q and now - q[0] > window_seconds:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def ip():
    return request.remote_addr or "unknown"
