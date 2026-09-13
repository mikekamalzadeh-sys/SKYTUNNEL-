import os
import json
import random
import string
from datetime import datetime, timedelta
from functools import wraps

import requests
import turso_serverless
from flask import Flask, request, session, redirect, url_for, render_template_string, flash

# ---------------------------------------------------------------------------
# تنظیمات
# ---------------------------------------------------------------------------

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

BOT_TOKEN = os.getenv("BOT_TOKEN", "69786607:U_ltmyh-8XS6RuBUsLNiIVi9l0Mq0aekXvE")
BASE_URL = "https://api.splus.ir/bot" + BOT_TOKEN
CONFIG_API = os.getenv("CONFIG_API", "https://su.randomatic.ir/api/v1/configs")
CONFIG_KEY = os.getenv("CONFIG_KEY", "sk_live_azIaKWpOvQDoD2-7vX8-yyf3WNPg6U1p")
ADMIN_ID = int(os.getenv("ADMIN_ID", "48198481"))

# مقادیر پیش‌فرض (fallback) — همون‌هایی که تو bot.py هم به‌عنوان fallback
# استفاده می‌شن. مقدار واقعی و قابل‌تغییر از صفحه‌ی «⚙️ مدیریت» (پایین‌تر)
# ذخیره می‌شه و همیشه با get_setting() از جدول settings خونده می‌شه — چون این
# دیتابیس بین پنل سایت و ربات مشترکه، تغییر از هرکدوم فوراً روی اون یکی هم
# اثر می‌ذاره.
DEFAULT_BOT_USERNAME = os.getenv("BOT_USERNAME", "")  # مثلا SkyTunnelBot (برای دکمه‌ی «باز کردن ربات»)
DEFAULT_PRICE_PER_GB_WIREGUARD = os.getenv("PRICE_PER_GB_WIREGUARD", "3500")
DEFAULT_PRICE_PER_GB_CONFIG = os.getenv("PRICE_PER_GB_CONFIG", "3500")
DEFAULT_PRICE_PER_GB_BOTH = os.getenv("PRICE_PER_GB_BOTH", "5500")
DEFAULT_CARD_NUMBER = os.getenv("CARD_NUMBER", "6219861957006504")
DEFAULT_CARD_OWNER = os.getenv("CARD_OWNER", "کمالزاده")
DEFAULT_MIN_TOPUP = os.getenv("MIN_TOPUP", "10000")

# ارسال پیامک OTP: اگه KAVENEGAR_API_KEY ست نشده باشه، به‌جای ارسال واقعی، کد
# تو صفحه نشون داده می‌شه («حالت آزمایشی») تا بدون سرویس پیامک هم قابل تست باشه.
KAVENEGAR_API_KEY = os.getenv("KAVENEGAR_API_KEY", "")
OTP_TTL_SECONDS = 180

app = Flask(__name__)
app.secret_key = os.getenv("SITE_SECRET_KEY", "sky-site-secret-change-me")

SESSION = requests.Session()


# ---------------------------------------------------------------------------
# دیتابیس (همون Turso که bot.py و پنل ادمین استفاده می‌کنن)
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


def normalize_phone(raw):
    """دقیقاً هم‌منطق با bot.py — تا شماره‌ی وارد شده تو سایت با شماره‌ی ثبت‌شده
    از طریق دکمه‌ی اشتراک‌گذاری تو ربات یکسان مقایسه بشه."""
    if not raw:
        return raw
    digits = "".join(ch for ch in raw if ch.isdigit())
    if digits.startswith("0098"):
        digits = digits[2:]
    if digits.startswith("98") and len(digits) >= 12:
        return "+" + digits
    if digits.startswith("0") and len(digits) == 11:
        return "+98" + digits[1:]
    if len(digits) == 10 and digits.startswith("9"):
        return "+98" + digits
    return "+" + digits if digits else raw


@with_db_retry
def ensure_otp_table():
    conn = get_conn()
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS otp_codes (
        phone TEXT PRIMARY KEY,
        code TEXT,
        expires_at TEXT,
        attempts INTEGER DEFAULT 0
    )""")
    conn.commit()
    conn.close()


@with_db_retry
def get_user_id_by_phone(phone):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT user_id FROM users WHERE phone=?", (phone,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None


@with_db_retry
def save_otp(phone, code):
    conn = get_conn()
    c = conn.cursor()
    expires_at = (datetime.now() + timedelta(seconds=OTP_TTL_SECONDS)).isoformat()
    c.execute(
        "INSERT INTO otp_codes (phone, code, expires_at, attempts) VALUES (?, ?, ?, 0) "
        "ON CONFLICT(phone) DO UPDATE SET code=excluded.code, expires_at=excluded.expires_at, attempts=0",
        (phone, code, expires_at),
    )
    conn.commit()
    conn.close()


@with_db_retry
def get_otp(phone):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT code, expires_at, attempts FROM otp_codes WHERE phone=?", (phone,))
    res = c.fetchone()
    conn.close()
    return res


@with_db_retry
def bump_otp_attempts(phone):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE phone=?", (phone,))
    conn.commit()
    conn.close()


@with_db_retry
def clear_otp(phone):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM otp_codes WHERE phone=?", (phone,))
    conn.commit()
    conn.close()


@with_db_retry
def get_wallet(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else 0


@with_db_retry
def update_wallet(user_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, wallet) VALUES (?, 0)", (user_id,))
    c.execute("UPDATE users SET wallet = wallet + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


@with_db_retry
def get_user_configs(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        """SELECT id, sub_url, config_id, gb, label, days, price, created_at, expires_at, type
           FROM user_configs WHERE user_id=? ORDER BY created_at DESC""",
        (user_id,),
    )
    res = c.fetchall()
    conn.close()
    return res


@with_db_retry
def save_user_config(user_id, sub_url, config_id, gb, label, days, price, ctype="both"):
    conn = get_conn()
    c = conn.cursor()
    expires_at = (datetime.now() + timedelta(days=days)).isoformat()
    c.execute(
        """INSERT INTO user_configs
           (user_id, sub_url, config_id, gb, label, days, price, type, created_at, expires_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, sub_url, config_id, gb, label, days, price, ctype, datetime.now().isoformat(), expires_at),
    )
    conn.commit()
    conn.close()


@with_db_retry
def get_discount_code(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT code, percent, max_uses, used_count, active FROM discount_codes WHERE code=?",
        (code.strip().upper(),),
    )
    res = c.fetchone()
    conn.close()
    return res


@with_db_retry
def increment_discount_usage(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE discount_codes SET used_count = used_count + 1 WHERE code=?", (code.strip().upper(),))
    conn.commit()
    conn.close()


def validate_discount_code(code):
    if not code:
        return True, 0
    row = get_discount_code(code)
    if not row:
        return False, "❌ کد تخفیف نامعتبر است."
    _, percent, max_uses, used_count, active = row
    if active != 1:
        return False, "⛔ این کد تخفیف غیرفعال شده است."
    if used_count >= max_uses:
        return False, "⚠️ ظرفیت استفاده از این کد تخفیف تمام شده است."
    return True, percent


@with_db_retry
def get_setting(key, default="1"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else default


def is_feature_enabled(key):
    return get_setting(key, "1") == "1"


@with_db_retry
def set_setting(key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )
    conn.commit()
    conn.close()


def get_config_types():
    """قیمت هر گیگ برای هر نوع سرویس — همیشه تازه از settings خونده می‌شه تا
    تغییرات صفحه‌ی مدیریت بدون نیاز به ری‌استارت روی فروشگاه اعمال بشه."""
    return {
        "wireguard": {"label": "🔒 فقط وایرگارد", "proto": "wireguard",
                      "price_per_gb": int(get_setting("price_wireguard", DEFAULT_PRICE_PER_GB_WIREGUARD))},
        "config": {"label": "⚙️ فقط کانفیگ", "proto": "xray",
                   "price_per_gb": int(get_setting("price_config", DEFAULT_PRICE_PER_GB_CONFIG))},
        "both": {"label": "🔀 هر دو (پرسرعت‌ترین)", "proto": "both",
                 "price_per_gb": int(get_setting("price_both", DEFAULT_PRICE_PER_GB_BOTH))},
    }


def get_card_number():
    return get_setting("card_number", DEFAULT_CARD_NUMBER)


def get_card_owner():
    return get_setting("card_owner", DEFAULT_CARD_OWNER)


def get_min_topup():
    return int(get_setting("min_topup", DEFAULT_MIN_TOPUP))


def get_bot_username():
    return get_setting("bot_username", DEFAULT_BOT_USERNAME)


# کلیدهای سوییچ‌های فعال/غیرفعال‌سازی که هم تو ربات (bot.py) و هم تو این پنل
# خونده می‌شن — با هم اسم و توضیح فارسی‌شون برای نمایش تو صفحه‌ی مدیریت.
FEATURE_TOGGLES = [
    ("buy", "🛍 خرید کانفیگ"),
    ("topup", "💳 شارژ کیف پول"),
    ("trial", "🎁 تست رایگان"),
    ("delete_config", "🗑 حذف کانفیگ"),
    ("support", "🎧 پشتیبانی"),
]


def make_config(gb, label, days, proto="both"):
    try:
        res = SESSION.post(
            CONFIG_API,
            headers={"Authorization": "Bearer " + CONFIG_KEY},
            json={"gb": gb, "label": label, "expiryDays": days, "proto": proto},
            timeout=20,
        )
        if res.status_code in (200, 201):
            data = res.json()
            return {
                "success": True,
                "sub_url": data.get("subUrl"),
                "config_id": data.get("id") or data.get("uuid") or data.get("username"),
            }
        return {"success": False, "error": str(res.status_code)}
    except Exception as e:
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# پیامک OTP
# ---------------------------------------------------------------------------

def generate_otp():
    return "".join(random.choices(string.digits, k=5))


def send_sms(phone, code):
    """اگه سرویس پیامک تنظیم نشده باشه، فقط لاگ می‌کنه و کد تو صفحه نشون داده
    می‌شه (حالت آزمایشی). با ست‌کردن KAVENEGAR_API_KEY ارسال واقعی فعال می‌شه."""
    text = f"کد ورود شما به سایت: {code}"
    if not KAVENEGAR_API_KEY:
        print(f"📱 [حالت آزمایشی - بدون سرویس پیامک] کد برای {phone}: {code}")
        return True, None
    try:
        r = requests.post(
            f"https://api.kavenegar.com/v1/{KAVENEGAR_API_KEY}/sms/send.json",
            data={"receptor": phone, "message": text},
            timeout=10,
        )
        return r.status_code == 200, None
    except Exception as e:
        return False, str(e)


# ---------------------------------------------------------------------------
# احراز هویت
# ---------------------------------------------------------------------------

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


def is_admin():
    return bool(session.get("user_id")) and session["user_id"] == ADMIN_ID


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login"))
        if not is_admin():
            flash("⛔ شما به این بخش دسترسی ندارید.")
            return redirect(url_for("shop"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# قالب پایه — طراحی فانتزی، شیشه‌ای، RTL
# ---------------------------------------------------------------------------

BASE_HTML = """
<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SkyTunnel — فروشگاه کانفیگ</title>
<style>
  * { box-sizing: border-box; }
  body {
    margin: 0; min-height: 100vh; font-family: Tahoma, Vazirmatn, sans-serif;
    color: #eef1fb;
    background: radial-gradient(circle at 15% 10%, #2b2470 0%, transparent 45%),
                radial-gradient(circle at 85% 0%, #0e6f6b 0%, transparent 40%),
                radial-gradient(circle at 50% 100%, #1c1250 0%, transparent 55%),
                linear-gradient(160deg, #05040f 0%, #0b0a22 55%, #05040f 100%);
    background-attachment: fixed;
  }
  a { color: inherit; }
  .navbar {
    display: flex; align-items: center; justify-content: space-between;
    padding: 18px 26px; backdrop-filter: blur(14px);
    background: rgba(255,255,255,0.04); border-bottom: 1px solid rgba(255,255,255,0.08);
    position: sticky; top: 0; z-index: 20;
  }
  .brand { font-size: 20px; font-weight: 800; letter-spacing: .5px;
    background: linear-gradient(90deg, #8be9fd, #bd93f9, #ff79c6);
    -webkit-background-clip: text; background-clip: text; color: transparent; }
  .nav-links { display: flex; gap: 6px; align-items: center; }
  .nav-links a { padding: 9px 14px; border-radius: 10px; text-decoration: none; font-size: 14px; color: #c9cdf0; }
  .nav-links a.active, .nav-links a:hover { background: rgba(255,255,255,0.08); color: #fff; }
  .wallet-pill {
    background: linear-gradient(90deg, rgba(139,233,253,.18), rgba(189,147,249,.18));
    border: 1px solid rgba(255,255,255,0.15); border-radius: 30px; padding: 7px 16px;
    font-size: 13.5px; font-weight: 700;
  }
  .wrap { max-width: 960px; margin: 0 auto; padding: 34px 20px 60px; }
  .glass {
    background: rgba(255,255,255,0.045); border: 1px solid rgba(255,255,255,0.09);
    border-radius: 22px; padding: 24px 26px; backdrop-filter: blur(16px);
    box-shadow: 0 18px 50px rgba(0,0,0,0.35);
  }
  h1.page-title { font-size: 24px; margin: 0 0 22px; }
  .cards-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(230px, 1fr)); gap: 16px; margin-bottom: 26px; }
  .type-card {
    border-radius: 18px; padding: 20px; cursor: pointer;
    border: 2px solid rgba(255,255,255,0.09); background: rgba(255,255,255,0.03);
    transition: all .15s ease; position: relative;
  }
  .type-card:hover { border-color: rgba(139,233,253,0.5); transform: translateY(-2px); }
  .type-card.selected { border-color: #8be9fd; background: rgba(139,233,253,0.08); box-shadow: 0 0 0 3px rgba(139,233,253,0.15); }
  .type-card .t-label { font-size: 16px; font-weight: 700; margin-bottom: 8px; }
  .type-card .t-price { font-size: 13px; color: #a9aee0; }
  .type-card .t-price b { color: #8be9fd; font-size: 15px; }
  label { display: block; font-size: 13px; color: #a9aee0; margin: 14px 0 6px; }
  input[type=text], input[type=number], input[type=tel] {
    width: 100%; background: rgba(0,0,0,0.25); border: 1px solid rgba(255,255,255,0.14);
    color: #eef1fb; border-radius: 12px; padding: 12px 14px; font-size: 15px;
  }
  input:focus { outline: none; border-color: #8be9fd; }
  .btn {
    display: inline-block; text-decoration: none; cursor: pointer; border: none;
    background: linear-gradient(90deg, #8be9fd, #bd93f9); color: #0b0a22; font-weight: 800;
    padding: 13px 26px; border-radius: 14px; font-size: 15px; box-shadow: 0 8px 24px rgba(139,233,253,0.25);
  }
  .btn.secondary { background: rgba(255,255,255,0.08); color: #eef1fb; box-shadow: none; border: 1px solid rgba(255,255,255,0.14); }
  .btn.block { width: 100%; text-align: center; }
  .price-box {
    margin-top: 20px; padding: 18px 20px; border-radius: 16px;
    background: linear-gradient(90deg, rgba(139,233,253,.1), rgba(189,147,249,.1));
    border: 1px solid rgba(255,255,255,0.12); display: flex; justify-content: space-between; align-items: center;
  }
  .price-box .amount { font-size: 22px; font-weight: 800; color: #8be9fd; }
  .flash { background: rgba(80,220,140,0.14); border: 1px solid rgba(80,220,140,0.4); color: #8ff0b8;
    padding: 12px 16px; border-radius: 12px; margin-bottom: 18px; font-size: 14px; }
  .flash.error { background: rgba(255,100,100,0.14); border-color: rgba(255,100,100,0.4); color: #ff9d9d; }
  .config-item { border: 1px solid rgba(255,255,255,0.1); border-radius: 16px; padding: 16px 18px; margin-bottom: 12px; background: rgba(255,255,255,0.03); }
  .config-item .row { display: flex; justify-content: space-between; flex-wrap: wrap; gap: 6px; font-size: 13.5px; color: #b7bbe6; margin-bottom: 10px; }
  .config-item .link-box { display: flex; gap: 8px; }
  .config-item .link-box input { flex: 1; font-size: 12px; direction: ltr; text-align: left; }
  .center { text-align: center; }
  .muted { color: #8d92c2; font-size: 13px; }
  .login-wrap { min-height: 90vh; display: flex; align-items: center; justify-content: center; }
  .login-box { width: 360px; max-width: 92vw; }
  .login-box h2 { text-align: center; font-size: 22px; margin-top: 0; }
  .otp-hint { text-align: center; margin-top: 14px; font-size: 13px; color: #ffd580; background: rgba(255,213,128,0.08); border: 1px dashed rgba(255,213,128,0.4); border-radius: 12px; padding: 10px; }
  .toggle-list { display: flex; flex-direction: column; gap: 10px; }
  .toggle-row { display: flex; align-items: center; gap: 10px; cursor: pointer; font-size: 14.5px;
    background: rgba(255,255,255,0.03); border: 1px solid rgba(255,255,255,0.08); border-radius: 12px; padding: 12px 14px; }
  .toggle-row input[type=checkbox] { width: 18px; height: 18px; accent-color: #8be9fd; }
</style>
</head>
<body>
{{ body|safe }}
<script>
function selectType(el, key) {
  document.querySelectorAll('.type-card').forEach(c => c.classList.remove('selected'));
  el.classList.add('selected');
  document.getElementById('ctype').value = key;
  recalc();
}
function recalc() {
  try {
    var prices = JSON.parse(document.getElementById('price-data').textContent);
    var ctype = document.getElementById('ctype').value;
    var gb = parseFloat(document.getElementById('gb').value) || 0;
    var perGb = prices[ctype] || 0;
    var total = Math.round(gb * perGb);
    document.getElementById('price-amount').textContent = total.toLocaleString('en-US') + ' تومان';
  } catch(e) {}
}
window.addEventListener('DOMContentLoaded', function() {
  var gbInput = document.getElementById('gb');
  if (gbInput) gbInput.addEventListener('input', recalc);
});
</script>
</body>
</html>
"""


def navbar(active):
    logged_in = bool(session.get("user_id"))
    wallet_html = ""
    if logged_in:
        wallet = get_wallet(session["user_id"])
        wallet_html = f'<span class="wallet-pill">💳 {wallet:,} تومان</span>'
    links = ""
    if logged_in:
        pages = [("shop", "shop", "🛍 فروشگاه"), ("configs", "my_configs", "🗂 کانفیگ‌های من")]
        if is_admin():
            pages.append(("admin", "admin_panel", "⚙️ مدیریت"))
        pages.append(("logout", "logout", "خروج"))
    else:
        pages = [("login", "login", "ورود")]
    for key, endpoint, label in pages:
        cls = "active" if active == key else ""
        links += f'<a href="{url_for(endpoint)}" class="{cls}">{label}</a>'
    return f"""
    <div class="navbar">
      <div class="brand">🌤 SkyTunnel</div>
      <div class="nav-links">{links}{wallet_html}</div>
    </div>
    """


def render_page(title, active, content_html, wrap=True):
    flashes = "".join(f'<div class="flash">{m}</div>' for m in get_flashed())
    inner = f'<h1 class="page-title">{title}</h1>{flashes}{content_html}' if title else f"{flashes}{content_html}"
    body = navbar(active) + (f'<div class="wrap">{inner}</div>' if wrap else inner)
    return render_template_string(BASE_HTML, body=body)


def get_flashed():
    from flask import get_flashed_messages
    return get_flashed_messages()


# ---------------------------------------------------------------------------
# صفحات
# ---------------------------------------------------------------------------

LANDING_HTML = """
<div class="wrap">
  <div class="glass center" style="padding:60px 30px;">
    <div style="font-size:44px;">🌤</div>
    <h1 style="font-size:30px;margin:14px 0 10px;">فروشگاه اینترنتی SkyTunnel</h1>
    <p class="muted" style="max-width:480px;margin:0 auto 26px;">
      کانفیگ پرسرعت بخر، حسابت رو به ربات سروش وصل کن و از همون کیف پول تو سایت و تو ربات استفاده کن.
    </p>
    <a class="btn" href="{{ url_for('login') }}">ورود با شماره موبایل 📱</a>
  </div>
</div>
"""


@app.route("/")
def home():
    if session.get("user_id"):
        return redirect(url_for("shop"))
    return render_template_string(BASE_HTML, body=navbar("home") + LANDING_HTML)


LOGIN_HTML = """
<div class="login-wrap">
  <div class="glass login-box">
    <h2>📱 ورود به سایت</h2>
    {% if error %}<div class="flash error">{{ error }}</div>{% endif %}
    <form method="post">
      <label>شماره موبایل</label>
      <input type="tel" name="phone" placeholder="09xxxxxxxxx" required>
      <div style="margin-top:20px;"><button class="btn block" type="submit">دریافت کد تایید</button></div>
    </form>
    <p class="muted center" style="margin-top:16px;">
      اگه اولین باره، اول تو ربات سروش دکمه‌ی «🔗 اتصال به سایت» رو بزن و شماره‌ات رو به اشتراک بذار.
    </p>
  </div>
</div>
"""


@app.route("/login", methods=["GET", "POST"])
def login():
    ensure_otp_table()
    error = None
    if request.method == "POST":
        phone = normalize_phone(request.form.get("phone", ""))
        if not phone or len(phone) < 8:
            error = "شماره‌ی وارد شده معتبر نیست."
        else:
            code = generate_otp()
            save_otp(phone, code)
            ok, err = send_sms(phone, code)
            session["pending_phone"] = phone
            session["dev_otp"] = code if not KAVENEGAR_API_KEY else None
            return redirect(url_for("verify"))
    return render_template_string(BASE_HTML, body=navbar("login") + render_template_string(LOGIN_HTML, error=error))


VERIFY_HTML = """
<div class="login-wrap">
  <div class="glass login-box">
    <h2>🔐 کد تایید</h2>
    {% if error %}<div class="flash error">{{ error }}</div>{% endif %}
    <p class="muted center">کد ۵ رقمی ارسال‌شده به {{ phone }} رو وارد کن:</p>
    <form method="post">
      <label>کد تایید</label>
      <input type="text" name="code" maxlength="5" inputmode="numeric" required>
      <div style="margin-top:20px;"><button class="btn block" type="submit">ورود</button></div>
    </form>
    {% if dev_otp %}
    <div class="otp-hint">🧪 حالت آزمایشی (سرویس پیامک تنظیم نشده) — کد شما: <b>{{ dev_otp }}</b></div>
    {% endif %}
  </div>
</div>
"""


@app.route("/verify", methods=["GET", "POST"])
def verify():
    phone = session.get("pending_phone")
    if not phone:
        return redirect(url_for("login"))
    error = None
    if request.method == "POST":
        entered = request.form.get("code", "").strip()
        row = get_otp(phone)
        if not row:
            error = "کد منقضی شده، دوباره تلاش کنید."
        else:
            code, expires_at, attempts = row
            if attempts >= 5:
                error = "تعداد تلاش‌ها بیش از حد مجاز بود. دوباره درخواست کد بدید."
            elif datetime.fromisoformat(expires_at) < datetime.now():
                error = "کد منقضی شده، دوباره تلاش کنید."
            elif entered != code:
                bump_otp_attempts(phone)
                error = "کد وارد شده اشتباه است."
            else:
                clear_otp(phone)
                user_id = get_user_id_by_phone(phone)
                if not user_id:
                    error = "این شماره هنوز به هیچ حسابی تو ربات وصل نیست. اول تو ربات سروش دکمه‌ی «🔗 اتصال به سایت» رو بزنید."
                else:
                    session.pop("pending_phone", None)
                    session.pop("dev_otp", None)
                    session["user_id"] = user_id
                    return redirect(url_for("shop"))
    dev_otp = session.get("dev_otp")
    content = render_template_string(VERIFY_HTML, error=error, phone=phone, dev_otp=dev_otp)
    return render_template_string(BASE_HTML, body=navbar("login") + content)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("home"))


SHOP_HTML = """
{% if not buy_enabled %}
  <div class="glass center"><p>⛔ خرید کانفیگ در حال حاضر توسط مدیریت غیرفعال است.</p></div>
{% else %}
<div class="glass">
  <form method="post" action="{{ url_for('buy') }}">
    <div class="cards-grid">
      {% for key, t in types.items() %}
      <div class="type-card {{ 'selected' if key=='both' else '' }}" onclick="selectType(this, '{{ key }}')">
        <div class="t-label">{{ t.label }}</div>
        <div class="t-price">هر گیگ: <b>{{ '{:,}'.format(t.price_per_gb) }}</b> تومان</div>
      </div>
      {% endfor %}
    </div>
    <input type="hidden" id="ctype" name="ctype" value="both">
    <script id="price-data" type="application/json">{{ prices_json|safe }}</script>

    <label>حجم (گیگابایت)</label>
    <input type="number" id="gb" name="gb" min="1" value="10" required>

    <label>مدت اعتبار (روز)</label>
    <input type="number" name="days" min="1" value="30" required>

    <label>کد تخفیف (اختیاری)</label>
    <input type="text" name="discount" placeholder="مثلاً SUMMER20">

    <div class="price-box">
      <span class="muted">مبلغ قابل پرداخت</span>
      <span class="amount" id="price-amount">—</span>
    </div>

    <div style="margin-top:20px;"><button class="btn block" type="submit">خرید و پرداخت از کیف پول</button></div>
    <p class="muted center" style="margin-top:10px;">💳 موجودی فعلی شما: {{ '{:,}'.format(wallet) }} تومان — برای شارژ کیف پول از ربات سروش استفاده کنید.</p>
  </form>
</div>
<script>recalc();</script>
{% endif %}
"""


@app.route("/shop")
@login_required
def shop():
    wallet = get_wallet(session["user_id"])
    config_types = get_config_types()
    prices_json = json.dumps({k: v["price_per_gb"] for k, v in config_types.items()})
    content = render_template_string(
        SHOP_HTML,
        types=config_types,
        wallet=wallet,
        prices_json=prices_json,
        buy_enabled=is_feature_enabled("buy"),
    )
    return render_page("🛍 خرید کانفیگ", "shop", content)


@app.route("/shop/buy", methods=["POST"])
@login_required
def buy():
    if not is_feature_enabled("buy"):
        flash("⛔ خرید کانفیگ در حال حاضر غیرفعال است.")
        return redirect(url_for("shop"))

    user_id = session["user_id"]
    config_types = get_config_types()
    ctype = request.form.get("ctype", "both")
    if ctype not in config_types:
        ctype = "both"
    try:
        gb = float(request.form.get("gb", "0"))
        days = int(request.form.get("days", "0"))
    except ValueError:
        flash("مقادیر وارد شده معتبر نیستند.")
        return redirect(url_for("shop"))

    if gb <= 0 or days <= 0:
        flash("حجم و مدت باید بزرگ‌تر از صفر باشند.")
        return redirect(url_for("shop"))

    discount_code = request.form.get("discount", "").strip()
    percent = 0
    if discount_code:
        ok, result = validate_discount_code(discount_code)
        if not ok:
            flash(result)
            return redirect(url_for("shop"))
        percent = result

    type_info = config_types[ctype]
    price = round(gb * type_info["price_per_gb"])
    if percent:
        price = round(price * (100 - percent) / 100)

    wallet = get_wallet(user_id)
    if wallet < price:
        flash(f"موجودی کیف پول کافی نیست. مبلغ لازم: {price:,} تومان — موجودی شما: {wallet:,} تومان.")
        return redirect(url_for("shop"))

    label = f"site-{user_id}"
    result = make_config(gb, label, days, proto=type_info["proto"])
    if not result["success"] or not result.get("sub_url"):
        flash("❌ خطا در ارتباط با سرور پنل. لطفاً دوباره تلاش کنید.")
        return redirect(url_for("shop"))

    update_wallet(user_id, -price)
    save_user_config(user_id, result["sub_url"], result["config_id"], gb, label, days, price, ctype)
    if discount_code and percent:
        increment_discount_usage(discount_code)

    flash(f"🎉 کانفیگ شما ساخته شد! حجم: {gb} گیگابایت، مدت: {days} روز — لینک تو صفحه‌ی «کانفیگ‌های من» موجوده.")
    return redirect(url_for("my_configs"))


CONFIGS_HTML = """
{% if configs %}
  {% for c in configs %}
  <div class="config-item">
    <div class="row">
      <span>🏷 {{ c.label }}</span>
      <span>📦 {{ c.gb }} GB</span>
      <span>⏳ {{ c.days }} روز</span>
      <span>💵 {{ '{:,}'.format(c.price) }} تومان</span>
    </div>
    <div class="link-box">
      <input type="text" readonly value="{{ c.sub_url }}" onclick="this.select();">
      <a class="btn secondary" href="{{ c.sub_url }}" target="_blank">باز کردن</a>
    </div>
  </div>
  {% endfor %}
{% else %}
  <div class="glass center"><p class="muted">هنوز هیچ کانفیگی خریداری نکرده‌اید.</p></div>
{% endif %}
"""


@app.route("/configs")
@login_required
def my_configs():
    rows = get_user_configs(session["user_id"])
    configs = [
        {
            "sub_url": r[1], "config_id": r[2], "gb": r[3], "label": r[4],
            "days": r[5], "price": r[6], "created_at": r[7], "expires_at": r[8], "type": r[9],
        }
        for r in rows
    ]
    content = render_template_string(CONFIGS_HTML, configs=configs)
    return render_page("🗂 کانفیگ‌های من", "configs", content)


ADMIN_HTML = """
<div class="glass">
  <form method="post">
    <h2 style="margin-top:0;">💰 قیمت‌گذاری (تومان به‌ازای هر گیگ)</h2>
    <label>قیمت هر گیگ — فقط وایرگارد</label>
    <input type="number" name="price_wireguard" min="0" value="{{ s.price_wireguard }}" required>
    <label>قیمت هر گیگ — فقط کانفیگ</label>
    <input type="number" name="price_config" min="0" value="{{ s.price_config }}" required>
    <label>قیمت هر گیگ — هر دو</label>
    <input type="number" name="price_both" min="0" value="{{ s.price_both }}" required>

    <h2>💳 اطلاعات پرداخت</h2>
    <label>شماره کارت</label>
    <input type="text" name="card_number" value="{{ s.card_number }}" required>
    <label>نام صاحب کارت</label>
    <input type="text" name="card_owner" value="{{ s.card_owner }}" required>
    <label>حداقل مبلغ شارژ کیف پول</label>
    <input type="number" name="min_topup" min="0" value="{{ s.min_topup }}" required>

    <h2>🤖 تنظیمات ربات</h2>
    <label>یوزرنیم ربات (بدون @) — برای دکمه‌ی «باز کردن ربات»</label>
    <input type="text" name="bot_username" value="{{ s.bot_username }}" placeholder="مثلاً SkyTunnelBot">

    <h2>🎚 فعال / غیرفعال‌سازی بخش‌ها</h2>
    <div class="toggle-list">
      {% for key, label in toggles %}
      <label class="toggle-row">
        <input type="checkbox" name="feat_{{ key }}" {{ 'checked' if features[key] else '' }}>
        <span>{{ label }}</span>
      </label>
      {% endfor %}
    </div>

    <div style="margin-top:24px;"><button class="btn block" type="submit">💾 ذخیره‌ی تغییرات</button></div>
  </form>
</div>
"""


@app.route("/admin", methods=["GET", "POST"])
@admin_required
def admin_panel():
    if request.method == "POST":
        for key in ("price_wireguard", "price_config", "price_both", "min_topup"):
            try:
                value = int(request.form.get(key, "0"))
            except ValueError:
                value = 0
            set_setting(key, max(0, value))

        set_setting("card_number", request.form.get("card_number", "").strip())
        set_setting("card_owner", request.form.get("card_owner", "").strip())
        set_setting("bot_username", request.form.get("bot_username", "").strip())

        for key, _label in FEATURE_TOGGLES:
            set_setting(key, "1" if request.form.get(f"feat_{key}") else "0")

        flash("✅ تنظیمات با موفقیت ذخیره شد.")
        return redirect(url_for("admin_panel"))

    config_types = get_config_types()
    s = {
        "price_wireguard": config_types["wireguard"]["price_per_gb"],
        "price_config": config_types["config"]["price_per_gb"],
        "price_both": config_types["both"]["price_per_gb"],
        "card_number": get_card_number(),
        "card_owner": get_card_owner(),
        "min_topup": get_min_topup(),
        "bot_username": get_bot_username(),
    }
    features = {key: is_feature_enabled(key) for key, _label in FEATURE_TOGGLES}
    content = render_template_string(ADMIN_HTML, s=s, toggles=FEATURE_TOGGLES, features=features)
    return render_page("⚙️ پنل مدیریت", "admin", content)


if __name__ == "__main__":
    ensure_otp_table()
    port = int(os.getenv("PORT", "8090"))
    app.run(host="0.0.0.0", port=port)
