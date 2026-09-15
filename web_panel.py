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

# مقادیر پیش‌فرض (fallback) قیمت/کارت/حداقل شارژ — دقیقاً هم‌نام با کلیدهایی
# که bot.py هم به‌عنوان fallback استفاده می‌کنه. مقدار واقعی و قابل‌تغییر از
# صفحه‌ی «تنظیمات ربات» همین پنل ذخیره می‌شه و چون جدول settings بین این پنل و
# bot.py مشترکه، تغییر از اینجا فوراً روی خود ربات هم اثر می‌ذاره.
DEFAULT_PRICE_PER_GB_WIREGUARD = os.getenv("PRICE_PER_GB_WIREGUARD", "3500")
DEFAULT_PRICE_PER_GB_CONFIG = os.getenv("PRICE_PER_GB_CONFIG", "3500")
DEFAULT_PRICE_PER_GB_BOTH = os.getenv("PRICE_PER_GB_BOTH", "5500")
DEFAULT_PRICE_PER_GB_OPENVPN = os.getenv("PRICE_PER_GB_OPENVPN", "4000")
DEFAULT_CARD_NUMBER = os.getenv("CARD_NUMBER", "6219861957006504")
DEFAULT_CARD_OWNER = os.getenv("CARD_OWNER", "کمالزاده")
DEFAULT_MIN_TOPUP = os.getenv("MIN_TOPUP", "10000")

CONFIG_SETTINGS = [
    ("price_wireguard", "قیمت هر گیگ — فقط وایرگارد (تومان)", DEFAULT_PRICE_PER_GB_WIREGUARD, "number"),
    ("price_config", "قیمت هر گیگ — فقط کانفیگ (تومان)", DEFAULT_PRICE_PER_GB_CONFIG, "number"),
    ("price_both", "قیمت هر گیگ — هر دو (تومان)", DEFAULT_PRICE_PER_GB_BOTH, "number"),
    ("price_openvpn", "قیمت هر گیگ — فقط OpenVPN (تومان)", DEFAULT_PRICE_PER_GB_OPENVPN, "number"),
    ("card_number", "شماره کارت", DEFAULT_CARD_NUMBER, "text"),
    ("card_owner", "نام صاحب کارت", DEFAULT_CARD_OWNER, "text"),
    ("min_topup", "حداقل مبلغ شارژ کیف پول (تومان)", DEFAULT_MIN_TOPUP, "number"),
]

# متن‌های قابل‌تغییر ربات، دسته‌بندی‌شده بر اساس صفحه‌ای که تو ربات نشون داده
# می‌شن. هر آیتم: (کلید settings بدون پیشوند text_, عنوان و راهنمای placeholder
# تو پنل, مقدار پیش‌فرض، تعداد ردیف textarea).
# ⚠️ توجه: این مقدارهای پیش‌فرض باید دقیقاً با DEFAULT_* داخل bot.py یکی
# باشن، چون اگه ادمین چیزی تو پنل ذخیره نکرده باشه، bot.py از همون
# پیش‌فرض‌های خودش (نه این‌جا) استفاده می‌کنه؛ این‌جا فقط برای نمایش تو پنله.
BOT_TEXT_GROUPS = [
    ("🏠 پیام /start و منوی اصلی (همون تصویری که می‌بینید)", [
        ("welcome_intro", "تیتر خوش‌آمدگویی (همون خط اول، داخل «{welcome_intro}» در قالب پایین)",
         "به ربات فروش خوش آمدید", 2),
        ("main_menu", "قالب کامل پیام منوی اصلی — placeholder های مجاز: "
         "{welcome_intro} {wireguard_price} {config_price} {both_price} {openvpn_price} {wallet} {divider}",
         "✨ <b>{welcome_intro}</b>\n{divider}\n"
         "🔒 فقط وایرگارد: <b>{wireguard_price}</b> تومان/گیگ\n"
         "⚙️ فقط کانفیگ: <b>{config_price}</b> تومان/گیگ\n"
         "🔀 هر دو: <b>{both_price}</b> تومان/گیگ\n"
         "📱 فقط OpenVPN: <b>{openvpn_price}</b> تومان/گیگ\n"
         "💳 موجودی کیف پول: <b>{wallet}</b> تومان", 8),
    ]),
    ("🛍 خرید کانفیگ", [
        ("buy_menu", "قالب پیام «انتخاب نوع سرویس» — placeholder ها: "
         "{wireguard_price} {config_price} {both_price} {openvpn_price} {wallet} {divider}",
         "🛍 نوع سرویس مورد نظر را انتخاب کنید:\n{divider}\n"
         "🔒 فقط وایرگارد: <b>{wireguard_price}</b> تومان/گیگ\n"
         "⚙️ فقط کانفیگ: <b>{config_price}</b> تومان/گیگ\n"
         "🔀 هر دو: <b>{both_price}</b> تومان/گیگ\n"
         "📱 فقط OpenVPN: <b>{openvpn_price}</b> تومان/گیگ\n\n"
         "💳 موجودی فعلی: <b>{wallet}</b> تومان", 8),
        ("buy_insufficient", "پیام «موجودی کافی نیست» — placeholder: {wallet}",
         "⚠️ موجودی کافی نیست.\n💳 موجودی فعلی: <b>{wallet}</b> تومان", 2),
    ]),
    ("👤 حساب من", [
        ("account_info", "قالب صفحه‌ی حساب کاربری — placeholder ها: "
         "{chat_id} {wallet} {total_gb} {total_price} {divider}",
         "👤 <b>حساب کاربری شما</b>\n{divider}\n"
         "🆔 شناسه: <code>{chat_id}</code>\n"
         "💳 موجودی کیف پول: <b>{wallet}</b> تومان\n"
         "📊 مجموع خرید: <b>{total_gb}</b> گیگابایت\n"
         "💵 مجموع پرداختی: <b>{total_price}</b> تومان", 6),
    ]),
    ("🗂 کانفیگ‌های من / حذف کانفیگ", [
        ("myconfigs_header", "تیتر بالای لیست کانفیگ‌ها", "🗂 <b>لیست کانفیگ‌های شما</b>", 2),
        ("myconfigs_empty", "پیام وقتی کاربر هنوز کانفیگی ندارد", "📭 هنوز هیچ کانفیگی ثبت نکرده‌اید.", 2),
        ("delete_prompt", "پیام شروع فرآیند حذف کانفیگ", "🗑 شماره ردیف کانفیگ مورد نظر برای حذف را ارسال کنید:", 2),
        ("delete_empty", "پیام وقتی کانفیگی برای حذف وجود ندارد", "📭 کانفیگی برای حذف وجود ندارد.", 2),
    ]),
    ("🧪 تست رایگان", [
        ("trial_used", "پیام وقتی کاربر قبلاً از تست رایگان استفاده کرده", "⚠️ شما پیش‌تر از تست رایگان استفاده کرده‌اید.", 2),
        ("trial_building", "پیام «در حال ساخت کانفیگ تست»", "⏳ در حال ساخت کانفیگ تست...", 2),
        ("trial_ready", "قالب پیام تحویل کانفیگ تست — placeholder ها: {sub_url} {divider}",
         "🎁 <b>کانفیگ تست رایگان شما آماده شد</b>\n{divider}\n"
         "📶 حجم: <b>0.1</b> گیگابایت\n"
         "⏳ مدت اعتبار: <b>1</b> روز\n{divider}\n"
         "🔗 لینک سابسکریپشن:\n<code>{sub_url}</code>", 6),
        ("trial_error", "پیام خطای ساخت کانفیگ تست", "❌ خطا در ساخت کانفیگ تست. لطفاً بعداً دوباره تلاش کنید.", 2),
    ]),
    ("💠 شارژ کیف پول", [
        ("topup_prompt", "قالب پیام درخواست مبلغ شارژ — placeholder: {min_topup}",
         "💠 مبلغ مورد نظر برای شارژ کیف پول را به تومان وارد کنید.\n"
         "حداقل مبلغ شارژ: <b>{min_topup}</b> تومان", 3),
        ("topup_card_info", "قالب پیام اطلاعات کارت برای واریز — placeholder ها: "
         "{amount} {card_number} {card_owner} {divider}",
         "💳 <b>اطلاعات پرداخت</b>\n{divider}\n"
         "💵 مبلغ: <b>{amount}</b> تومان\n"
         "💳 شماره کارت: <code>{card_number}</code>\n"
         "👤 به نام: <b>{card_owner}</b>\n\n"
         "📸 پس از واریز، تصویر رسید را ارسال کنید تا برای بررسی به پشتیبانی ارجاع داده شود.", 7),
        ("topup_need_photo", "پیام «لطفاً تصویر رسید را ارسال کنید»", "📸 لطفاً تصویر رسید واریزی را ارسال کنید.", 2),
        ("topup_receipt_ok", "پیام «رسید دریافت شد و در حال بررسی است»",
         "✅ رسید شما دریافت شد و برای بررسی ارسال گردید. پس از تایید، کیف پول شما شارژ خواهد شد.", 2),
    ]),
    ("🎧 پشتیبانی", [
        ("support_prompt", "پیام شروع ارسال تیکت پشتیبانی", "🎧 پیام خود را برای پشتیبانی ارسال کنید:", 2),
        ("support_ticket_created", "قالب پیام تایید ثبت تیکت — placeholder: {tid}",
         "✅ پیام شما با شناسه تیکت #{tid} برای پشتیبانی ثبت شد و به‌زودی پاسخ داده می‌شود.", 2),
    ]),
]

# نسخه‌ی تخت (بدون دسته‌بندی) برای ذخیره‌سازی؛ از روی BOT_TEXT_GROUPS ساخته می‌شه
# تا مجبور نباشیم هر متن رو دوبار تعریف کنیم.
BOT_TEXTS = [(key, label, default) for _group, items in BOT_TEXT_GROUPS for key, label, default, _rows in items]

# متن دکمه‌های منوی اصلی: (کلید داخلی ثابت, عنوان تو پنل, متن پیش‌فرض دکمه)
BOT_BUTTONS = [
    ("buy", "دکمه‌ی خرید کانفیگ", "🛍 خرید کانفیگ"),
    ("trial", "دکمه‌ی تست رایگان", "🧪 تست رایگان"),
    ("topup", "دکمه‌ی شارژ کیف پول", "💠 شارژ کیف پول"),
    ("account", "دکمه‌ی حساب من", "👤 حساب من"),
    ("myconfigs", "دکمه‌ی کانفیگ‌های من", "🗂 کانفیگ‌های من"),
    ("delete", "دکمه‌ی حذف کانفیگ", "🗑 حذف کانفیگ"),
    ("support", "دکمه‌ی پشتیبانی", "🎧 پشتیبانی"),
]

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
  :root {
    --purple: #7c3aed;
    --purple-dark: #6d28d9;
    --purple-light: #ede9fe;
    --pink: #ec4899;
    --bg: #f4f2fb;
    --ink: #1e1b2e;
    --muted: #8a86a3;
    --card: #ffffff;
    --border: #ece9f7;
    --radius: 20px;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; font-family: Tahoma, Vazirmatn, sans-serif;
    background: var(--bg); color: var(--ink);
  }
  .app { max-width: 480px; margin: 0 auto; min-height: 100vh; position: relative;
    background: var(--bg); padding-bottom: 92px; }

  /* ---------- top bar ---------- */
  .topbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 16px 14px;
  }
  .topbar .brand { display: flex; align-items: center; gap: 10px; }
  .avatar {
    width: 42px; height: 42px; border-radius: 50%; flex-shrink: 0;
    background: linear-gradient(135deg, var(--purple), var(--pink));
    display: flex; align-items: center; justify-content: center;
    font-size: 18px; color: #fff;
  }
  .topbar h1 { font-size: 17px; margin: 0; }
  .topbar .sub { font-size: 12px; color: var(--muted); }
  .icon-btn {
    width: 40px; height: 40px; border-radius: 50%; background: var(--card);
    border: 1px solid var(--border); display: flex; align-items: center;
    justify-content: center; text-decoration: none; color: var(--ink); font-size: 16px;
    box-shadow: 0 2px 8px rgba(124,58,237,0.08);
  }

  /* ---------- cards ---------- */
  .content { padding: 4px 16px 10px; }
  .cards { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 12px; margin-bottom: 18px; }
  .card, .panel {
    background: var(--card); border: 1px solid var(--border); border-radius: var(--radius);
    padding: 16px 18px; box-shadow: 0 4px 18px rgba(124,58,237,0.06);
  }
  .card .icon-badge {
    width: 34px; height: 34px; border-radius: 50%; background: var(--purple-light);
    color: var(--purple); display: flex; align-items: center; justify-content: center;
    font-size: 15px; margin-bottom: 10px;
  }
  .card .label { color: var(--muted); font-size: 12.5px; margin-bottom: 4px; }
  .card .value { font-size: 20px; font-weight: 800; }
  .panel { margin-bottom: 16px; }

  .banner {
    border-radius: var(--radius); padding: 16px 18px; margin-bottom: 16px;
    background: linear-gradient(90deg, var(--purple), var(--pink));
    color: #fff; display: flex; align-items: center; justify-content: space-between;
    font-size: 13.5px; font-weight: 600;
  }

  table { width: 100%; border-collapse: collapse; background: var(--card); border-radius: 16px; overflow: hidden; }
  th, td { padding: 11px 12px; text-align: right; border-bottom: 1px solid var(--border); font-size: 13px; }
  th { background: var(--purple-light); color: var(--purple-dark); font-weight: 700; }
  tr:last-child td { border-bottom: none; }

  input[type=text], input[type=number], input[type=password], textarea, select {
    background: var(--card); border: 1.5px solid var(--border); color: var(--ink);
    border-radius: 14px; padding: 11px 14px; font-size: 14px; width: 100%;
  }
  input:focus, textarea:focus, select:focus { outline: none; border-color: var(--purple); }
  textarea { min-height: 100px; resize: vertical; font-family: inherit; }
  label { display: block; margin: 12px 0 6px; font-size: 13px; color: var(--muted); font-weight: 600; }

  button, .btn {
    background: var(--purple); color: #fff; border: none; border-radius: 999px;
    padding: 11px 20px; font-size: 13.5px; font-weight: 700; cursor: pointer;
    text-decoration: none; display: inline-block;
  }
  button.secondary, .btn.secondary { background: var(--purple-light); color: var(--purple-dark); }
  button.danger, .btn.danger { background: #fde3e3; color: #dc2626; }
  .btn.block, button.block { width: 100%; text-align: center; }

  .badge { padding: 4px 11px; border-radius: 999px; font-size: 11.5px; font-weight: 700; }
  .badge.on { background: #dcfce7; color: #16a34a; }
  .badge.off { background: #fde3e3; color: #dc2626; }
  .badge.open { background: #fef3c7; color: #b45309; }
  .badge.closed { background: var(--purple-light); color: var(--muted); }
  .badge.answered { background: #dcfce7; color: #16a34a; }

  .flash { background: #dcfce7; color: #16a34a; padding: 11px 16px; border-radius: 14px; margin-bottom: 14px; font-size: 13.5px; font-weight: 600; }
  .flex { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
  .pager { display: flex; gap: 8px; margin-top: 14px; }
  small.muted { color: var(--muted); }
  .muted { color: var(--muted); }

  /* ---------- bottom nav ---------- */
  .bottom-nav {
    position: fixed; bottom: 0; left: 50%; transform: translateX(-50%);
    width: 100%; max-width: 480px; background: var(--card);
    border-top: 1px solid var(--border); display: flex; justify-content: space-around;
    padding: 8px 4px 10px; box-shadow: 0 -4px 20px rgba(124,58,237,0.08); z-index: 40;
  }
  .bottom-nav a {
    display: flex; flex-direction: column; align-items: center; gap: 3px;
    color: var(--muted); text-decoration: none; font-size: 11px; flex: 1;
  }
  .bottom-nav a .ic { font-size: 19px; }
  .bottom-nav a.active { color: var(--purple); font-weight: 700; }

  /* ---------- drawer ---------- */
  .drawer-overlay {
    display: none; position: fixed; inset: 0; background: rgba(30,27,46,0.45); z-index: 50;
  }
  .drawer-overlay:target { display: block; }
  .drawer {
    position: absolute; bottom: 0; left: 50%; transform: translateX(-50%);
    width: 100%; max-width: 480px; background: var(--card);
    border-radius: 24px 24px 0 0; padding: 10px 18px 26px; max-height: 82vh; overflow-y: auto;
  }
  .drawer .handle { width: 40px; height: 4px; background: var(--border); border-radius: 4px; margin: 8px auto 14px; }
  .drawer h3 { font-size: 15px; margin: 16px 0 8px; color: var(--muted); }
  .drawer a.drawer-link {
    display: flex; align-items: center; gap: 10px; padding: 12px 4px; text-decoration: none;
    color: var(--ink); font-size: 14.5px; font-weight: 600; border-bottom: 1px solid var(--border);
  }
  .drawer a.close-drawer {
    display: flex; align-items: center; justify-content: center; text-decoration: none;
    color: var(--muted); font-size: 20px;
  }
  .drawer-top { display: flex; justify-content: space-between; align-items: center; }

  /* ---------- login ---------- */
  .login-wrap { display: flex; align-items: center; justify-content: center; min-height: 100vh; }
  .login-box { background: var(--card); padding: 34px 28px; border-radius: 24px; width: 320px;
    box-shadow: 0 10px 40px rgba(124,58,237,0.15); }
  .login-box h2 { margin-top: 0; text-align: center; }
</style>
</head>
<body>
{{ body|safe }}
</body>
</html>
"""

# لینک‌های بخش «بیشتر» (منوی کشویی از پایین) — دسته‌بندی‌شده مثل صفحاتی که
# مرجع طراحی بودن
DRAWER_LINKS = """
<div id="more" class="drawer-overlay">
  <div class="drawer">
    <div class="handle"></div>
    <div class="drawer-top">
      <h2 style="margin:0;font-size:16px;">همه‌ی بخش‌ها</h2>
      <a href="#" class="close-drawer">✕</a>
    </div>

    <h3>مدیریت ربات</h3>
    <a class="drawer-link" href="{{ url_for('settings_page') }}">🔌 تنظیمات ربات</a>
    <a class="drawer-link" href="{{ url_for('appearance_page') }}">🎨 متن‌ها و ظاهر ربات</a>

    <h3>فروش</h3>
    <a class="drawer-link" href="{{ url_for('discount_codes') }}">🎟 کدهای تخفیف</a>
    <a class="drawer-link" href="{{ url_for('broadcast') }}">📣 پیام همگانی</a>

    <h3>حساب</h3>
    <a class="drawer-link" href="{{ url_for('logout') }}">🚪 خروج از پنل</a>
  </div>
</div>
"""

TOPBAR = """
<div class="topbar">
  <div class="brand">
    <div class="avatar">🌤</div>
    <div>
      <h1>SkyTunnel</h1>
      <div class="sub">پنل مدیریت</div>
    </div>
  </div>
  <a href="{{ url_for('logout') }}" class="icon-btn" title="خروج">⎋</a>
</div>
"""

BOTTOM_NAV = """
<div class="bottom-nav">
  <a href="{{ url_for('dashboard') }}" class="{{ 'active' if active=='dashboard' else '' }}"><span class="ic">📊</span>داشبورد</a>
  <a href="{{ url_for('users') }}" class="{{ 'active' if active=='users' else '' }}"><span class="ic">👥</span>کاربران</a>
  <a href="{{ url_for('tickets') }}" class="{{ 'active' if active=='tickets' else '' }}"><span class="ic">🎧</span>تیکت‌ها</a>
  <a href="#more" class="{{ 'active' if active in ('settings','appearance','discounts','broadcast') else '' }}"><span class="ic">☰</span>بیشتر</a>
</div>
"""


def render_page(title, active, content_html):
    body = f"""
    <div class="app">
      {render_template_string(TOPBAR)}
      <div class="content">
        <div class="flex" style="justify-content:space-between;margin-bottom:14px;">
          <h2 style="margin:0;font-size:18px;">{title}</h2>
        </div>
        {"".join(f'<div class="flash">{m}</div>' for m in get_flashed()) }
        {content_html}
      </div>
      {render_template_string(BOTTOM_NAV, active=active)}
      {render_template_string(DRAWER_LINKS)}
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

    attention_html = ""
    if s['open_tickets'] > 0:
        attention_html += f"""
        <a href="{url_for('tickets')}" class="drawer-link" style="border-bottom:1px solid var(--border);">
          <span class="icon-badge" style="margin:0;">🎧</span>
          <div style="flex:1;">
            <div style="font-weight:700;">تیکت‌های پاسخ‌نداده</div>
            <div class="muted" style="font-size:12px;">{s['open_tickets']} تیکت در انتظار پاسخ</div>
          </div>
        </a>
        """
    if s['pending_topups'] > 0:
        attention_html += f"""
        <a href="{url_for('tickets')}" class="drawer-link" style="border-bottom:none;">
          <span class="icon-badge" style="margin:0;">💳</span>
          <div style="flex:1;">
            <div style="font-weight:700;">شارژهای در انتظار تایید</div>
            <div class="muted" style="font-size:12px;">{s['pending_topups']} درخواست شارژ کیف پول</div>
          </div>
        </a>
        """
    if not attention_html:
        attention_html = '<p class="muted" style="margin:6px 0 0;">چیزی برای رسیدگی فوری نیست 🎉</p>'

    content = f"""
    <div class="banner">
      <span>🌤 خلاصه‌ی وضعیت فروشگاه</span>
      <span>امروز</span>
    </div>

    <div class="cards">
      <div class="card">
        <div class="icon-badge">👥</div>
        <div class="label">تعداد کاربران</div>
        <div class="value">{s['total_users']:,}</div>
      </div>
      <div class="card">
        <div class="icon-badge">💰</div>
        <div class="label">مجموع کیف‌پول‌ها</div>
        <div class="value">{s['total_wallet']:,}</div>
      </div>
      <div class="card">
        <div class="icon-badge">📦</div>
        <div class="label">کانفیگ فروخته‌شده</div>
        <div class="value">{s['total_configs']:,}</div>
      </div>
      <div class="card">
        <div class="icon-badge">📈</div>
        <div class="label">مجموع درآمد</div>
        <div class="value">{s['total_revenue']:,}</div>
      </div>
    </div>

    <div class="panel" style="padding:14px 16px 6px;">
      <div class="flex" style="justify-content:space-between;margin-bottom:2px;">
        <div style="font-weight:800;">🔔 نیاز به توجه</div>
      </div>
      {attention_html}
    </div>
    """
    return render_page("پیشخوان", "dashboard", content)


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

    config_rows_html = ""
    for key, label, default, input_type in CONFIG_SETTINGS:
        value = get_setting(key, default)
        config_rows_html += f"""
        <label>{label}</label>
        <input type="{input_type}" name="{key}" value="{value}" required>
        """

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
    <div class="panel">
      <h3 style="margin-top:0;">💰 قیمت‌گذاری و اطلاعات پرداخت</h3>
      <form method="post" action="{url_for('save_config')}">
        {config_rows_html}
        <div style="margin-top:14px;"><button type="submit">💾 ذخیره‌ی تغییرات</button></div>
      </form>
    </div>
    <table>
      <tr><th>قابلیت</th><th>وضعیت</th><th></th></tr>
      {rows_html}
    </table>
    """
    return render_page("تنظیمات ربات", "settings", content)


@app.route("/settings/save-config", methods=["POST"])
@login_required
def save_config():
    valid_keys = {key for key, *_ in CONFIG_SETTINGS}
    for key, _label, _default, input_type in CONFIG_SETTINGS:
        raw = request.form.get(key, "").strip()
        if input_type == "number":
            try:
                raw = str(max(0, int(raw)))
            except ValueError:
                flash(f"مقدار «{_label}» باید عدد باشد و ذخیره نشد.")
                continue
        if key in valid_keys and raw:
            set_setting(key, raw)
    flash("تنظیمات قیمت و پرداخت ذخیره شد.")
    return redirect(url_for("settings_page"))


@app.route("/settings/toggle/<key>", methods=["POST"])
@login_required
def toggle_setting(key):
    if key in FEATURES:
        current = get_setting(key, "1")
        set_setting(key, "0" if current == "1" else "1")
        flash(f"وضعیت «{FEATURES[key]}» تغییر کرد.")
    return redirect(url_for("settings_page"))


@app.route("/appearance")
@login_required
def appearance_page():
    ensure_settings_table()
    keyboard_style = get_setting("keyboard_style", "reply")

    texts_html = ""
    for group_title, items in BOT_TEXT_GROUPS:
        texts_html += f'<h4 style="margin-bottom:6px;">{group_title}</h4>'
        for key, label, default, rows in items:
            value = get_setting("text_" + key, default)
            texts_html += f"""
            <label>{label}</label>
            <textarea name="text_{key}" rows="{rows}">{value}</textarea>
            """

    buttons_html = ""
    for key, label, default in BOT_BUTTONS:
        value = get_setting("btn_" + key, default)
        buttons_html += f"""
        <label>{label}</label>
        <input type="text" name="btn_{key}" value="{value}" required>
        """

    content = f"""
    <div class="panel">
      <form method="post" action="{url_for('save_appearance')}">
        <h3 style="margin-top:0;">⌨️ نوع کیبورد منوی اصلی</h3>
        <div class="flex" style="margin-bottom:14px;">
          <label class="flex" style="cursor:pointer;">
            <input type="radio" name="keyboard_style" value="reply" {"checked" if keyboard_style == "reply" else ""}>
            <span>کیبورد ثابت پایین صفحه (روش فعلی)</span>
          </label>
          <label class="flex" style="cursor:pointer;">
            <input type="radio" name="keyboard_style" value="inline" {"checked" if keyboard_style == "inline" else ""}>
            <span>دکمه‌های شیشه‌ای زیر پیام (اینلاین)</span>
          </label>
        </div>

        <h3>💬 متن‌های ربات</h3>
        {texts_html}

        <h3>🔘 متن دکمه‌های منو</h3>
        {buttons_html}

        <div style="margin-top:14px;"><button type="submit">💾 ذخیره‌ی تغییرات</button></div>
      </form>
    </div>
    <p><small class="muted">این تغییرات مستقیم روی خود ربات تلگرام اعمال می‌شوند (بدون نیاز به ری‌استارت).</small></p>
    """
    return render_page("متن‌ها و ظاهر ربات", "appearance", content)


@app.route("/appearance/save", methods=["POST"])
@login_required
def save_appearance():
    style = request.form.get("keyboard_style", "reply")
    set_setting("keyboard_style", "inline" if style == "inline" else "reply")

    for key, _label, _default in BOT_TEXTS:
        value = request.form.get("text_" + key, "").strip()
        if value:
            set_setting("text_" + key, value)

    for key, _label, _default in BOT_BUTTONS:
        value = request.form.get("btn_" + key, "").strip()
        if value:
            set_setting("btn_" + key, value)

    flash("متن‌ها و ظاهر ربات ذخیره شد.")
    return redirect(url_for("appearance_page"))


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
