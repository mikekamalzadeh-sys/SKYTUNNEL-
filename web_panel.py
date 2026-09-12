import os
import json
import time
import threading
from datetime import datetime
from functools import wraps

import requests
import turso_serverless
from flask import Flask, request, session, redirect, url_for, render_template_string, flash

# ---------------------------------------------------------------------------
# تنظیمات (از Environment Variables می‌خونه، دقیقاً هم‌نام با bot.py)
# ---------------------------------------------------------------------------

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

BOT_TOKEN = os.getenv("BOT_TOKEN", "69786607:U_ltmyh-8XS6RuBUsLNiIVi9l0Mq0aekXvE")
BASE_URL = "https://api.splus.ir/bot" + BOT_TOKEN

PANEL_USERNAME = os.getenv("PANEL_USERNAME", "admin")
PANEL_PASSWORD = os.getenv("PANEL_PASSWORD", "change-me-please")

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "sky-panel-secret-change-me")

FEATURES = {
    "buy": "🛍 خرید کانفیگ",
    "trial": "🧪 تست رایگان",
    "topup": "💠 شارژ کیف پول",
    "delete_config": "🗑 حذف کانفیگ",
    "support": "🎧 پشتیبانی",
}

USERS_PAGE_SIZE = 20

SESSION = requests.Session()


# ---------------------------------------------------------------------------
# دیتابیس (همون Turso که bot.py و admin_sky_bot.py استفاده می‌کنن)
# ---------------------------------------------------------------------------

_db_conn = None


def get_conn():
    global _db_conn
    if _db_conn is None:
        _db_conn = turso_serverless.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
        _db_conn.close = lambda: None
    return _db_conn


def reset_conn():
    global _db_conn
    _db_conn = None


def with_db_retry(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if "stream not found" in msg or "stream_expired" in msg or ("404" in msg and "stream" in msg):
                reset_conn()
                return func(*args, **kwargs)
            raise
    return wrapper


@with_db_retry
def ensure_settings_table():
    conn = get_conn()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    conn.commit()
    conn.close()


@with_db_retry
def get_setting(key, default="1"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else default


@with_db_retry
def set_setting(key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


@with_db_retry
def get_dashboard_stats():
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM users")
    total_users = c.fetchone()[0]

    c.execute("SELECT SUM(wallet) FROM users")
    total_wallet = c.fetchone()[0] or 0

    c.execute("SELECT COUNT(*), SUM(gb), SUM(price) FROM user_configs")
    row = c.fetchone()
    total_configs = row[0] or 0
    total_gb = row[1] or 0
    total_revenue = row[2] or 0

    c.execute("SELECT COUNT(*) FROM tickets WHERE status='open'")
    open_tickets = c.fetchone()[0]

    c.execute("SELECT COUNT(*) FROM topup_requests WHERE status='pending'")
    pending_topups = c.fetchone()[0]

    conn.close()
    return {
        "total_users": total_users,
        "total_wallet": total_wallet,
        "total_configs": total_configs,
        "total_gb": total_gb or 0,
        "total_revenue": total_revenue,
        "open_tickets": open_tickets,
        "pending_topups": pending_topups,
    }


@with_db_retry
def get_users_count(search=None):
    conn = get_conn()
    c = conn.cursor()
    if search:
        like = f"%{search}%"
        c.execute(
            "SELECT COUNT(*) FROM users WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ?",
            (like, like, like),
        )
    else:
        c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    conn.close()
    return total


@with_db_retry
def get_users_page(offset=0, limit=USERS_PAGE_SIZE, search=None):
    conn = get_conn()
    c = conn.cursor()
    if search:
        like = f"%{search}%"
        c.execute(
            """SELECT user_id, username, first_name, wallet FROM users
               WHERE CAST(user_id AS TEXT) LIKE ? OR username LIKE ? OR first_name LIKE ?
               ORDER BY user_id LIMIT ? OFFSET ?""",
            (like, like, like, limit, offset),
        )
    else:
        c.execute(
            "SELECT user_id, username, first_name, wallet FROM users ORDER BY user_id LIMIT ? OFFSET ?",
            (limit, offset),
        )
    res = c.fetchall()
    conn.close()
    return res


@with_db_retry
def update_wallet(user_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, wallet) VALUES (?, ?)", (user_id, 0))
    c.execute("UPDATE users SET wallet = wallet + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


@with_db_retry
def get_wallet(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None


@with_db_retry
def ensure_discount_table():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS discount_codes (
        code TEXT PRIMARY KEY,
        percent INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TEXT
    )''')
    conn.commit()
    conn.close()


@with_db_retry
def create_discount_code(code, percent, max_uses):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO discount_codes (code, percent, max_uses, used_count, active, created_at) VALUES (?, ?, ?, 0, 1, ?)",
        (code.strip().upper(), percent, max_uses, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


@with_db_retry
def code_exists(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM discount_codes WHERE code=?", (code.strip().upper(),))
    res = c.fetchone()
    conn.close()
    return res is not None


@with_db_retry
def get_all_discount_codes():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT code, percent, max_uses, used_count, active FROM discount_codes ORDER BY created_at DESC")
    res = c.fetchall()
    conn.close()
    return res


@with_db_retry
def toggle_discount_code(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT active FROM discount_codes WHERE code=?", (code,))
    row = c.fetchone()
    if row:
        new_active = 0 if row[0] == 1 else 1
        c.execute("UPDATE discount_codes SET active=? WHERE code=?", (new_active, code))
        conn.commit()
    conn.close()


@with_db_retry
def delete_discount_code(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM discount_codes WHERE code=?", (code,))
    conn.commit()
    conn.close()


@with_db_retry
def get_all_user_ids():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users")
    res = [r[0] for r in c.fetchall()]
    conn.close()
    return res


@with_db_retry
def get_tickets(status_filter=None, limit=50):
    conn = get_conn()
    c = conn.cursor()
    if status_filter:
        c.execute(
            "SELECT id, user_id, message, status, admin_reply, created_at FROM tickets WHERE status=? ORDER BY id DESC LIMIT ?",
            (status_filter, limit),
        )
    else:
        c.execute(
            "SELECT id, user_id, message, status, admin_reply, created_at FROM tickets ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    res = c.fetchall()
    conn.close()
    return res


@with_db_retry
def get_ticket(ticket_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, user_id, message, status, admin_reply, created_at FROM tickets WHERE id=?", (ticket_id,))
    res = c.fetchone()
    conn.close()
    return res


@with_db_retry
def update_ticket_status(ticket_id, status, admin_reply=None):
    conn = get_conn()
    c = conn.cursor()
    if admin_reply:
        c.execute("UPDATE tickets SET status=?, admin_reply=? WHERE id=?", (status, admin_reply, ticket_id))
    else:
        c.execute("UPDATE tickets SET status=? WHERE id=?", (status, ticket_id))
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# ارسال پیام به کاربران (همون API که bot.py استفاده می‌کنه)
# ---------------------------------------------------------------------------

def send_telegram_message(chat_id, text):
    try:
        res = SESSION.post(
            BASE_URL + "/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        return res.json().get("ok", False)
    except Exception:
        return False


def broadcast_worker(text, user_ids):
    sent, failed = 0, 0
    for uid in user_ids:
        ok = send_telegram_message(uid, text)
        if ok:
            sent += 1
        else:
            failed += 1
        time.sleep(0.05)
    print(f"📣 پیام همگانی تمام شد: {sent} موفق، {failed} ناموفق")


# ---------------------------------------------------------------------------
# احراز هویت ساده
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# قالب پایه (RTL، تم تیره)
# ---------------------------------------------------------------------------

BASE_HTML = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>پنل مدیریت SkyTunnel</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: Tahoma, Vazirmatn, sans-serif;
    background: #0f1420; color: #e6e8ee;
  }
  .layout { display: flex; min-height: 100vh; }
  .sidebar {
    width: 220px; background: #151b2b; padding: 20px 14px;
    border-left: 1px solid #232b3d; flex-shrink: 0;
  }
  .sidebar h2 { font-size: 17px; margin: 0 0 24px; color: #7c9cff; }
  .sidebar a {
    display: block; color: #c3c9d9; text-decoration: none;
    padding: 10px 12px; border-radius: 8px; margin-bottom: 4px; font-size: 14px;
  }
  .sidebar a:hover, .sidebar a.active { background: #232b3d; color: #fff; }
  .main { flex: 1; padding: 24px 28px; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 22px; }
  .topbar h1 { font-size: 20px; margin: 0; }
  .logout { color: #ff8080; text-decoration: none; font-size: 13px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 14px; margin-bottom: 24px; }
  .card {
    background: #151b2b; border: 1px solid #232b3d; border-radius: 12px;
    padding: 16px 18px;
  }
  .card .label { color: #8a92a6; font-size: 13px; margin-bottom: 6px; }
  .card .value { font-size: 22px; font-weight: 700; }
  table { width: 100%; border-collapse: collapse; background: #151b2b; border-radius: 10px; overflow: hidden; }
  th, td { padding: 10px 12px; text-align: right; border-bottom: 1px solid #232b3d; font-size: 13.5px; }
  th { background: #1b2233; color: #8a92a6; font-weight: 600; }
  tr:last-child td { border-bottom: none; }
  .panel { background: #151b2b; border: 1px solid #232b3d; border-radius: 12px; padding: 18px 20px; margin-bottom: 18px; }
  input[type=text], input[type=number], input[type=password], textarea {
    background: #0f1420; border: 1px solid #2c364d; color: #e6e8ee;
    border-radius: 8px; padding: 9px 12px; font-size: 14px; width: 100%;
  }
  textarea { min-height: 120px; resize: vertical; font-family: inherit; }
  button, .btn {
    background: #3d5cff; color: #fff; border: none; border-radius: 8px;
    padding: 9px 16px; font-size: 13.5px; cursor: pointer; text-decoration: none; display: inline-block;
  }
  button.secondary, .btn.secondary { background: #2c364d; }
  button.danger, .btn.danger { background: #d94848; }
  .badge { padding: 3px 9px; border-radius: 20px; font-size: 12px; }
  .badge.on { background: #1c4a2e; color: #6bd88f; }
  .badge.off { background: #4a1c1c; color: #ff8f8f; }
  .badge.open { background: #4a3c1c; color: #ffcf6b; }
  .badge.closed { background: #2c364d; color: #a3acc2; }
  .badge.answered { background: #1c4a2e; color: #6bd88f; }
  .flash { background: #1c4a2e; color: #6bd88f; padding: 10px 14px; border-radius: 8px; margin-bottom: 16px; font-size: 14px; }
  .flex { display: flex; gap: 10px; align-items: center; }
  .pager { display: flex; gap: 8px; margin-top: 14px; }
  .login-wrap { display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .login-box { background: #151b2b; padding: 34px 30px; border-radius: 14px; width: 320px; border: 1px solid #232b3d; }
  .login-box h2 { margin-top: 0; text-align: center; }
  .login-box label { display: block; margin: 12px 0 6px; font-size: 13px; color: #8a92a6; }
  small.muted { color: #8a92a6; }
</style>
</head>
<body>
{{ body|safe }}
</body>
</html>
"""

SIDEBAR = """
<div class="sidebar">
  <h2>🌤 SkyTunnel</h2>
  <a href="{{ url_for('dashboard') }}" class="{{ 'active' if active=='dashboard' else '' }}">📊 داشبورد</a>
  <a href="{{ url_for('users') }}" class="{{ 'active' if active=='users' else '' }}">👥 کاربران</a>
  <a href="{{ url_for('tickets') }}" class="{{ 'active' if active=='tickets' else '' }}">🎧 تیکت‌های پشتیبانی</a>
  <a href="{{ url_for('settings_page') }}" class="{{ 'active' if active=='settings' else '' }}">🔌 تنظیمات ربات</a>
  <a href="{{ url_for('discount_codes') }}" class="{{ 'active' if active=='discounts' else '' }}">🎟 کدهای تخفیف</a>
  <a href="{{ url_for('broadcast') }}" class="{{ 'active' if active=='broadcast' else '' }}">📣 پیام همگانی</a>
</div>
"""


def render_page(title, active, content_html):
    body = f"""
    <div class="layout">
      {render_template_string(SIDEBAR, active=active)}
      <div class="main">
        <div class="topbar">
          <h1>{title}</h1>
          <a class="logout" href="{url_for('logout')}">خروج</a>
        </div>
        {"".join(f'<div class="flash">{m}</div>' for m in get_flashed()) }
        {content_html}
      </div>
    </div>
    """
    return render_template_string(BASE_HTML, body=body)


def get_flashed():
    from flask import get_flashed_messages
    return get_flashed_messages()


# ---------------------------------------------------------------------------
# صفحات
# ---------------------------------------------------------------------------

LOGIN_HTML = """
<div class="login-wrap">
  <form method="post" class="login-box">
    <h2>🌤 ورود به پنل</h2>
    {% if error %}<div class="flash" style="background:#4a1c1c;color:#ff8f8f;">{{ error }}</div>{% endif %}
    <label>نام کاربری</label>
    <input type="text" name="username" autocapitalize="off" autocorrect="off" spellcheck="false" required>
    <label>رمز عبور</label>
    <input type="password" name="password" autocapitalize="off" autocorrect="off" spellcheck="false" required>
    <div style="margin-top:18px;"><button type="submit" style="width:100%;">ورود</button></div>
  </form>
</div>
"""


@app.route("/debug-login-info")
def debug_login_info():
    def mask(v):
        if not v:
            return "(خالی است)"
        if len(v) <= 4:
            return f"طول={len(v)} مقدار کامل کوتاه است: {v}"
        return f"طول={len(v)} شروع می‌شود با «{v[:2]}» و تمام می‌شود با «{v[-2:]}»"

    return (
        "<div style='font-family:monospace;padding:30px;background:#0f1420;color:#e6e8ee;direction:ltr;'>"
        f"<p>PANEL_USERNAME: {mask(PANEL_USERNAME)}</p>"
        f"<p>PANEL_PASSWORD: {mask(PANEL_PASSWORD)}</p>"
        "<p style='color:#ff8f8f;'>این صفحه فقط برای عیب‌یابی است — بعد از حل مشکل باید حذف شود.</p>"
        "</div>"
    )


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        u = request.form.get("username", "").strip()
        p = request.form.get("password", "").strip()
        if u == PANEL_USERNAME.strip() and p == PANEL_PASSWORD.strip():
            session["logged_in"] = True
            return redirect(url_for("dashboard"))
        error = "نام کاربری یا رمز عبور اشتباه است."
    return render_template_string(BASE_HTML, body=render_template_string(LOGIN_HTML, error=error))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def dashboard():
    s = get_dashboard_stats()
    content = f"""
    <div class="cards">
      <div class="card"><div class="label">تعداد کاربران</div><div class="value">{s['total_users']:,}</div></div>
      <div class="card"><div class="label">مجموع موجودی کیف‌پول‌ها</div><div class="value">{s['total_wallet']:,} تومان</div></div>
      <div class="card"><div class="label">کانفیگ‌های فروخته‌شده</div><div class="value">{s['total_configs']:,}</div></div>
      <div class="card"><div class="label">مجموع درآمد</div><div class="value">{s['total_revenue']:,} تومان</div></div>
      <div class="card"><div class="label">تیکت‌های باز</div><div class="value">{s['open_tickets']:,}</div></div>
      <div class="card"><div class="label">شارژهای در انتظار</div><div class="value">{s['pending_topups']:,}</div></div>
    </div>
    """
    return render_page("داشبورد", "dashboard", content)


@app.route("/users")
@login_required
def users():
    search = request.args.get("q", "").strip()
    offset = int(request.args.get("offset", 0))
    total = get_users_count(search or None)
    rows = get_users_page(offset=offset, search=search or None)

    rows_html = ""
    for user_id, username, first_name, wallet in rows:
        handle = f"@{username}" if username else "-"
        name = first_name or "-"
        rows_html += f"""
        <tr>
          <td>{user_id}</td>
          <td>{handle}</td>
          <td>{name}</td>
          <td>{wallet:,} تومان</td>
          <td>
            <form method="post" action="{url_for('adjust_wallet')}" class="flex">
              <input type="hidden" name="user_id" value="{user_id}">
              <input type="number" name="amount" placeholder="مبلغ" style="width:110px;" required>
              <button type="submit" name="sign" value="1">➕</button>
              <button type="submit" name="sign" value="-1" class="secondary">➖</button>
            </form>
          </td>
        </tr>
        """

    pager = ""
    if offset > 0:
        pager += f'<a class="btn secondary" href="{url_for("users", q=search, offset=max(0, offset-USERS_PAGE_SIZE))}">صفحه قبل</a>'
    if offset + USERS_PAGE_SIZE < total:
        pager += f'<a class="btn secondary" href="{url_for("users", q=search, offset=offset+USERS_PAGE_SIZE)}">صفحه بعد</a>'

    content = f"""
    <div class="panel">
      <form method="get" class="flex">
        <input type="text" name="q" placeholder="جستجو با آیدی، یوزرنیم یا نام..." value="{search}">
        <button type="submit">جستجو</button>
      </form>
    </div>
    <table>
      <tr><th>آیدی</th><th>یوزرنیم</th><th>نام</th><th>موجودی</th><th>تغییر موجودی</th></tr>
      {rows_html if rows_html else '<tr><td colspan="5">کاربری یافت نشد.</td></tr>'}
    </table>
    <div class="pager">{pager}</div>
    <p><small class="muted">{total:,} کاربر ثبت‌شده</small></p>
    """
    return render_page("کاربران", "users", content)


@app.route("/users/wallet", methods=["POST"])
@login_required
def adjust_wallet():
    user_id = int(request.form["user_id"])
    amount = int(request.form["amount"])
    sign = int(request.form["sign"])
    if get_wallet(user_id) is None:
        flash("کاربر پیدا نشد.")
    else:
        update_wallet(user_id, amount * sign)
        flash(f"موجودی کاربر {user_id} به مقدار {amount:,} تومان {'افزایش' if sign > 0 else 'کاهش'} یافت.")
    return redirect(url_for("users"))


@app.route("/tickets")
@login_required
def tickets():
    status_filter = request.args.get("status", "open")
    rows = get_tickets(status_filter if status_filter != "all" else None)

    rows_html = ""
    for tid, user_id, message, status, admin_reply, created_at in rows:
        badge_class = status
        rows_html += f"""
        <tr>
          <td>#{tid}</td>
          <td>{user_id}</td>
          <td>{message[:60]}{'...' if len(message) > 60 else ''}</td>
          <td><span class="badge {badge_class}">{status}</span></td>
          <td><a class="btn secondary" href="{url_for('ticket_detail', ticket_id=tid)}">مشاهده</a></td>
        </tr>
        """

    tabs = ""
    for key, label in [("open", "باز"), ("answered", "پاسخ‌داده‌شده"), ("closed", "بسته"), ("all", "همه")]:
        active = "btn" if status_filter == key else "btn secondary"
        tabs += f'<a class="{active}" href="{url_for("tickets", status=key)}">{label}</a> '

    content = f"""
    <div class="panel flex">{tabs}</div>
    <table>
      <tr><th>شماره</th><th>کاربر</th><th>پیام</th><th>وضعیت</th><th></th></tr>
      {rows_html if rows_html else '<tr><td colspan="5">تیکتی یافت نشد.</td></tr>'}
    </table>
    """
    return render_page("تیکت‌های پشتیبانی", "tickets", content)


@app.route("/tickets/<int:ticket_id>")
@login_required
def ticket_detail(ticket_id):
    t = get_ticket(ticket_id)
    if not t:
        flash("تیکت پیدا نشد.")
        return redirect(url_for("tickets"))
    tid, user_id, message, status, admin_reply, created_at = t

    reply_block = f"<p><b>پاسخ قبلی:</b> {admin_reply}</p>" if admin_reply else ""

    content = f"""
    <div class="panel">
      <p><b>شماره:</b> #{tid} — <b>کاربر:</b> {user_id} — <span class="badge {status}">{status}</span></p>
      <p><b>پیام کاربر:</b><br>{message}</p>
      {reply_block}
      <form method="post" action="{url_for('ticket_reply', ticket_id=tid)}">
        <label>پاسخ به کاربر</label>
        <textarea name="reply" required></textarea>
        <div style="margin-top:10px;" class="flex">
          <button type="submit">ارسال پاسخ</button>
          <a class="btn danger" href="{url_for('ticket_close', ticket_id=tid)}">بستن تیکت بدون پاسخ</a>
          <a class="btn secondary" href="{url_for('tickets')}">بازگشت</a>
        </div>
      </form>
    </div>
    """
    return render_page(f"تیکت #{tid}", "tickets", content)


@app.route("/tickets/<int:ticket_id>/reply", methods=["POST"])
@login_required
def ticket_reply(ticket_id):
    reply_text = request.form.get("reply", "").strip()
    t = get_ticket(ticket_id)
    if not t:
        flash("تیکت پیدا نشد.")
        return redirect(url_for("tickets"))
    if reply_text:
        update_ticket_status(ticket_id, "answered", reply_text)
        send_telegram_message(t[1], f"📩 پاسخ پشتیبانی برای تیکت #{ticket_id}:\n\n{reply_text}")
        flash("پاسخ برای کاربر ارسال شد.")
    return redirect(url_for("tickets"))


@app.route("/tickets/<int:ticket_id>/close")
@login_required
def ticket_close(ticket_id):
    update_ticket_status(ticket_id, "closed")
    flash(f"تیکت #{ticket_id} بسته شد.")
    return redirect(url_for("tickets"))


@app.route("/settings")
@login_required
def settings_page():
    ensure_settings_table()
    rows_html = ""
    for key, label in FEATURES.items():
        enabled = get_setting(key, "1") == "1"
        badge = '<span class="badge on">روشن</span>' if enabled else '<span class="badge off">خاموش</span>'
        btn_label = "خاموش کن" if enabled else "روشن کن"
        btn_class = "danger" if enabled else ""
        rows_html += f"""
        <tr>
          <td>{label}</td>
          <td>{badge}</td>
          <td>
            <form method="post" action="{url_for('toggle_setting', key=key)}">
              <button type="submit" class="{btn_class}">{btn_label}</button>
            </form>
          </td>
        </tr>
        """
    content = f"""
    <table>
      <tr><th>قابلیت</th><th>وضعیت</th><th></th></tr>
      {rows_html}
    </table>
    """
    return render_page("تنظیمات ربات", "settings", content)


@app.route("/settings/toggle/<key>", methods=["POST"])
@login_required
def toggle_setting(key):
    if key in FEATURES:
        current = get_setting(key, "1")
        set_setting(key, "0" if current == "1" else "1")
        flash(f"وضعیت «{FEATURES[key]}» تغییر کرد.")
    return redirect(url_for("settings_page"))


DISCOUNT_LIST_HTML_HEADER = ""


@app.route("/discounts")
@login_required
def discount_codes():
    ensure_discount_table()
    codes = get_all_discount_codes()
    rows_html = ""
    for code, percent, max_uses, used_count, active in codes:
        badge = '<span class="badge on">فعال</span>' if active == 1 else '<span class="badge off">غیرفعال</span>'
        toggle_label = "غیرفعال کن" if active == 1 else "فعال کن"
        rows_html += f"""
        <tr>
          <td>{code}</td>
          <td>{percent}٪</td>
          <td>{used_count} از {max_uses}</td>
          <td>{badge}</td>
          <td class="flex">
            <form method="post" action="{url_for('toggle_discount', code=code)}">
              <button type="submit" class="secondary">{toggle_label}</button>
            </form>
            <form method="post" action="{url_for('delete_discount', code=code)}" onsubmit="return confirm('حذف این کد تخفیف؟');">
              <button type="submit" class="danger">حذف</button>
            </form>
          </td>
        </tr>
        """

    content = f"""
    <div class="panel">
      <form method="post" action="{url_for('create_discount')}" class="flex">
        <input type="text" name="code" placeholder="کد (مثل SUMMER20)" style="width:160px;" required>
        <input type="number" name="percent" placeholder="درصد تخفیف" style="width:130px;" min="1" max="100" required>
        <input type="number" name="max_uses" placeholder="سقف تعداد استفاده" style="width:150px;" min="1" required>
        <button type="submit">ساخت کد تخفیف</button>
      </form>
    </div>
    <table>
      <tr><th>کد</th><th>درصد</th><th>مصرف</th><th>وضعیت</th><th></th></tr>
      {rows_html if rows_html else '<tr><td colspan="5">هنوز کد تخفیفی ثبت نشده.</td></tr>'}
    </table>
    """
    return render_page("کدهای تخفیف", "discounts", content)


@app.route("/discounts/create", methods=["POST"])
@login_required
def create_discount():
    code = request.form.get("code", "").strip()
    try:
        percent = int(request.form.get("percent", ""))
        max_uses = int(request.form.get("max_uses", ""))
    except ValueError:
        flash("درصد و سقف استفاده باید عدد باشند.")
        return redirect(url_for("discount_codes"))

    if not code:
        flash("کد نمی‌تواند خالی باشد.")
    elif code_exists(code):
        flash("این کد از قبل وجود دارد.")
    elif not (1 <= percent <= 100):
        flash("درصد تخفیف باید بین ۱ تا ۱۰۰ باشد.")
    else:
        create_discount_code(code, percent, max_uses)
        flash(f"کد «{code.upper()}» ساخته شد.")
    return redirect(url_for("discount_codes"))


@app.route("/discounts/<code>/toggle", methods=["POST"])
@login_required
def toggle_discount(code):
    toggle_discount_code(code)
    flash(f"وضعیت کد «{code}» تغییر کرد.")
    return redirect(url_for("discount_codes"))


@app.route("/discounts/<code>/delete", methods=["POST"])
@login_required
def delete_discount(code):
    delete_discount_code(code)
    flash(f"کد «{code}» حذف شد.")
    return redirect(url_for("discount_codes"))


@app.route("/broadcast", methods=["GET", "POST"])
@login_required
def broadcast():
    if request.method == "POST":
        text = request.form.get("text", "").strip()
        if not text:
            flash("متن پیام نمی‌تواند خالی باشد.")
        else:
            user_ids = get_all_user_ids()
            threading.Thread(target=broadcast_worker, args=(text, user_ids), daemon=True).start()
            flash(f"ارسال پیام همگانی برای {len(user_ids):,} کاربر شروع شد (در پس‌زمینه ادامه می‌یابد).")
        return redirect(url_for("broadcast"))

    content = f"""
    <div class="panel">
      <form method="post">
        <label>متن پیام همگانی (فرمت HTML تلگرام پشتیبانی می‌شود، مثل &lt;b&gt;بولد&lt;/b&gt;)</label>
        <textarea name="text" required></textarea>
        <div style="margin-top:12px;"><button type="submit">ارسال به همه‌ی کاربران</button></div>
      </form>
    </div>
    <p><small class="muted">این پیام برای همه‌ی کاربرانی که تا الان با ربات فروش /start زده‌اند ارسال می‌شود.</small></p>
    """
    return render_page("پیام همگانی", "broadcast", content)


if __name__ == "__main__":
    ensure_settings_table()
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)
