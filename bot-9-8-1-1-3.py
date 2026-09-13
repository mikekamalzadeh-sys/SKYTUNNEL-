import requests
import time
import json
import os
import turso_serverless
from datetime import datetime, timedelta

TOKEN = os.getenv("BOT_TOKEN", "69786607:U_ltmyh-8XS6RuBUsLNiIVi9l0Mq0aekXvE")
BASE_URL = "https://api.splus.ir/bot" + TOKEN
CONFIG_API = os.getenv("CONFIG_API", "https://su.randomatic.ir/api/v1/configs")
CONFIG_KEY = os.getenv("CONFIG_KEY", "sk_live_azIaKWpOvQDoD2-7vX8-yyf3WNPg6U1p")
ADMIN_ID = int(os.getenv("ADMIN_ID", "48198481"))

# مقادیر پیش‌فرض (fallback) — اگه از پنل وب مقداری تو جدول settings ثبت نشده
# باشه، همین مقادیر استفاده می‌شن. مقدار واقعی و قابل‌تغییر همیشه با
# get_setting() خونده می‌شه (پایین‌تر) تا بدون نیاز به ری‌استارت ربات، از پنل
# قابل تغییر باشه.
DEFAULT_CARD_NUMBER = os.getenv("CARD_NUMBER", "6219861957006504")
DEFAULT_CARD_OWNER = os.getenv("CARD_OWNER", "کمالزاده")
DEFAULT_MIN_TOPUP = os.getenv("MIN_TOPUP", "10000")

# ---------------------------------------------------------------------------
# Service types & per-GB pricing
# ---------------------------------------------------------------------------
# مقدار "proto" طبق مستندات پنل CONFIG_API:
#   both      -> پیش‌فرض، هم xray هم wireguard ساخته می‌شه
#   xray      -> فقط xray، هیچ فایل وایرگاردی ساخته نمی‌شه ("فقط کانفیگ")
#   wireguard -> فقط وایرگارد؛ در این حالت بدنه‌ی sub_url خودِ کانفیگ
#                وایرگارده و ساب‌لینک واقعی وجود نداره
#   openvpn   -> فایل‌های مستقل .ovpn؛ از اعتبار گیگ کم می‌کنه و در صورت
#                بسته بودن فروش جدید توسط پنل با proto_unavailable رد می‌شه
DEFAULT_PRICE_PER_GB_WIREGUARD = os.getenv("PRICE_PER_GB_WIREGUARD", "3500")
DEFAULT_PRICE_PER_GB_CONFIG = os.getenv("PRICE_PER_GB_CONFIG", "3500")
DEFAULT_PRICE_PER_GB_BOTH = os.getenv("PRICE_PER_GB_BOTH", "5500")
DEFAULT_PRICE_PER_GB_OPENVPN = os.getenv("PRICE_PER_GB_OPENVPN", "4000")

# CONFIG_TYPES دیگه یه dict ثابت نیست — get_config_types() هر بار قیمت‌های
# فعلی رو از جدول settings می‌خونه تا تغییرات پنل وب فوراً روی ربات هم اعمال
# بشه (بدون نیاز به ری‌استارت).
def get_config_types():
    return {
        'wireguard': {'label': '🔒 فقط وایرگارد', 'proto': 'wireguard',
                      'price_per_gb': int(get_setting('price_wireguard', DEFAULT_PRICE_PER_GB_WIREGUARD))},
        'config':    {'label': '⚙️ فقط کانفیگ',   'proto': 'xray',
                      'price_per_gb': int(get_setting('price_config', DEFAULT_PRICE_PER_GB_CONFIG))},
        'both':      {'label': '🔀 هر دو',         'proto': 'both',
                      'price_per_gb': int(get_setting('price_both', DEFAULT_PRICE_PER_GB_BOTH))},
        'openvpn':   {'label': '📱 فقط OpenVPN',   'proto': 'openvpn',
                      'price_per_gb': int(get_setting('price_openvpn', DEFAULT_PRICE_PER_GB_OPENVPN))},
    }


def get_card_number():
    return get_setting('card_number', DEFAULT_CARD_NUMBER)


def get_card_owner():
    return get_setting('card_owner', DEFAULT_CARD_OWNER)


def get_min_topup():
    return int(get_setting('min_topup', DEFAULT_MIN_TOPUP))

TURSO_DATABASE_URL = os.getenv("TURSO_DATABASE_URL")
TURSO_AUTH_TOKEN = os.getenv("TURSO_AUTH_TOKEN")

DIVIDER = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

# ---------------------------------------------------------------------------
# HTTP session (keep-alive)
# ---------------------------------------------------------------------------
# قبلاً هر درخواست (requests.get/post) یه اتصال TCP+TLS تازه باز می‌کرد.
# وقتی سرور splus.ir یا شبکه بین راه‌وی و اون کند/پرلتنسی باشه (که با توجه به
# فیلترینگ/VPN طبیعیه)، این هندشیک تکراری روی هر پیام چند ثانیه اضافه می‌کنه.
# با یه Session مشترک، اتصال‌ها نگه‌داشته (keep-alive) و دوباره استفاده می‌شن.
SESSION = requests.Session()
_adapter = requests.adapters.HTTPAdapter(pool_connections=10, pool_maxsize=10, max_retries=0)
SESSION.mount('https://', _adapter)
SESSION.mount('http://', _adapter)

user_steps = {}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

_db_conn = None


def get_conn():
    global _db_conn
    if _db_conn is None:
        _db_conn = turso_serverless.connect(TURSO_DATABASE_URL, auth_token=TURSO_AUTH_TOKEN)
        _db_conn.close = lambda: None  # اتصال مشترکه؛ close() فراخوانی‌های قدیمی رو بی‌اثر می‌کنیم
    return _db_conn


def reset_conn():
    global _db_conn
    _db_conn = None


def with_db_retry(func):
    """اگه stream اتصال Turso به‌خاطر بی‌کاری منقضی شده باشه (خطای 404 'stream not
    found')، اتصال رو ریست می‌کنه و همون عملیات رو یک بار دیگه اجرا می‌کنه؛ برای
    هر خطای دیگه، خطا رو دوباره پرتاب می‌کنه تا رفتار قبلی حفظ بشه."""
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except Exception as e:
            msg = str(e).lower()
            if 'stream not found' in msg or 'stream_expired' in msg or ('404' in msg and 'stream' in msg):
                print('♻️ اتصال دیتابیس (stream) منقضی شده بود؛ اتصال تازه ساخته و درخواست دوباره اجرا شد.')
                reset_conn()
                return func(*args, **kwargs)
            raise
    return wrapper


@with_db_retry
def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, wallet INTEGER DEFAULT 0)')
    c.execute('''CREATE TABLE IF NOT EXISTS user_configs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        sub_url TEXT,
        config_id TEXT,
        gb REAL,
        label TEXT,
        days INTEGER,
        price INTEGER,
        type TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT,
        expires_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        message TEXT,
        status TEXT DEFAULT "open",
        admin_reply TEXT,
        created_at TEXT
    )''')
    c.execute('CREATE TABLE IF NOT EXISTS free_trials (user_id INTEGER PRIMARY KEY)')
    c.execute('''CREATE TABLE IF NOT EXISTS topup_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        amount INTEGER,
        status TEXT DEFAULT "pending",
        created_at TEXT
    )''')
    c.execute('CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)')
    c.execute('''CREATE TABLE IF NOT EXISTS discount_codes (
        code TEXT PRIMARY KEY,
        percent INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TEXT
    )''')
    for statement in (
        'ALTER TABLE users ADD COLUMN username TEXT',
        'ALTER TABLE users ADD COLUMN first_name TEXT',
        'ALTER TABLE users ADD COLUMN joined_at TEXT',
        'ALTER TABLE user_configs ADD COLUMN type TEXT',
        "ALTER TABLE user_configs ADD COLUMN status TEXT DEFAULT 'active'",
    ):
        try:
            c.execute(statement)
        except Exception:
            pass
    conn.commit()
    conn.close()


@with_db_retry
def get_wallet_db(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT wallet FROM users WHERE user_id=?', (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else 0


@with_db_retry
def add_user(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, wallet, joined_at) VALUES (?, ?, ?)',
               (user_id, 0, datetime.now().isoformat()))
    conn.commit()
    conn.close()


@with_db_retry
def upsert_user_info(user_id, username, first_name):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''INSERT INTO users (user_id, wallet, joined_at, username, first_name)
        VALUES (?, 0, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, first_name=excluded.first_name''',
        (user_id, datetime.now().isoformat(), username, first_name))
    conn.commit()
    conn.close()


@with_db_retry
def update_wallet_db(user_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, wallet) VALUES (?, ?)', (user_id, 0))
    c.execute('UPDATE users SET wallet = wallet + ? WHERE user_id=?', (amount, user_id))
    conn.commit()
    conn.close()


@with_db_retry
def save_user_config(user_id, sub_url, config_id, gb, label, days, price, ctype='both'):
    conn = get_conn()
    c = conn.cursor()
    expires_at = (datetime.now() + timedelta(days=days)).isoformat()
    c.execute('''INSERT INTO user_configs
        (user_id, sub_url, config_id, gb, label, days, price, type, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (user_id, sub_url, config_id, gb, label, days, price, ctype, datetime.now().isoformat(), expires_at))
    conn.commit()
    conn.close()


@with_db_retry
def get_user_configs(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''SELECT id, sub_url, config_id, gb, label, days, price, created_at, expires_at, type, status
        FROM user_configs WHERE user_id=? ORDER BY created_at DESC''', (user_id,))
    res = c.fetchall()
    conn.close()
    return res


@with_db_retry
def get_config_row(user_id, row_id):
    """یک کانفیگ مشخص از یک کاربر مشخص رو برمی‌گردونه (برای دکمه‌های مدیریت)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute('''SELECT id, sub_url, config_id, gb, label, days, price, created_at, expires_at, type, status
        FROM user_configs WHERE user_id=? AND id=?''', (user_id, row_id))
    res = c.fetchone()
    conn.close()
    return res


@with_db_retry
def update_config_label_db(row_id, label):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE user_configs SET label=? WHERE id=?', (label, row_id))
    conn.commit()
    conn.close()


@with_db_retry
def update_config_status_db(row_id, status):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE user_configs SET status=? WHERE id=?', (status, row_id))
    conn.commit()
    conn.close()


@with_db_retry
def update_config_suburl_db(row_id, sub_url):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE user_configs SET sub_url=? WHERE id=?', (sub_url, row_id))
    conn.commit()
    conn.close()


@with_db_retry
def extend_config_db(row_id, extra_gb, extra_days):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT gb, days, expires_at FROM user_configs WHERE id=?', (row_id,))
    row = c.fetchone()
    if row:
        gb, days, expires_at = row
        new_gb = (gb or 0) + extra_gb
        new_days = (days or 0) + extra_days
        try:
            base = datetime.fromisoformat(expires_at) if expires_at else datetime.now()
        except Exception:
            base = datetime.now()
        new_expires = (base + timedelta(days=extra_days)).isoformat()
        c.execute('UPDATE user_configs SET gb=?, days=?, expires_at=? WHERE id=?',
                   (new_gb, new_days, new_expires, row_id))
        conn.commit()
    conn.close()


def delete_config_from_panel(config_id):
    if not config_id:
        return True
    try:
        res = SESSION.delete(
            CONFIG_API + '/' + str(config_id),
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            timeout=15
        )
        return res.status_code in (200, 201, 204)
    except Exception:
        return False


def _panel_action(method, path_suffix, config_id, json_body=None):
    """کمکی مشترک برای عملیات مدیریتی پنل (تمدید/تغییر نام/توقف/ازسرگیری/تغییر لینک)."""
    try:
        res = SESSION.request(
            method,
            CONFIG_API + '/' + str(config_id) + path_suffix,
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            json=json_body,
            timeout=20
        )
        if res.status_code in (200, 201):
            try:
                return {'success': True, 'data': res.json()}
            except Exception:
                return {'success': True, 'data': {}}
        return {'success': False, 'error': res.text[:300]}
    except Exception as e:
        return {'success': False, 'error': str(e)}


def extend_config_on_panel(config_id, gb, days):
    return _panel_action('POST', '/extend', config_id, {'gb': gb, 'days': days})


def rename_config_on_panel(config_id, label):
    return _panel_action('PUT', '/label', config_id, {'label': label})


def pause_config_on_panel(config_id):
    return _panel_action('POST', '/pause', config_id)


def resume_config_on_panel(config_id):
    return _panel_action('POST', '/resume', config_id)


def rotate_link_config_on_panel(config_id):
    return _panel_action('POST', '/rotate-link', config_id)


def get_config_usage_gb(config_id):
    """
    مقدار مصرف (گیگابایت) یک کانفیگ رو از پنل می‌گیره.
    طبق مستندات پنل (GET /configs/:id)، پاسخ مستقیماً شامل فیلد usedGb
    (بر حسب گیگابایت، نه بایت) هست. برای اطمینان، هم حالت پاسخ مستقیم
    (آبجکت تکی) و هم حالت لیست‌شده (داخل configs[0]) رو پشتیبانی می‌کنه.
    در صورت هر نوع خطا یا نامشخص بودن فیلد، None برمی‌گردونه.
    """
    if not config_id:
        return None
    try:
        res = SESSION.get(
            CONFIG_API + '/' + str(config_id),
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            timeout=15
        )
        if res.status_code not in (200, 201):
            print('⚠️ خطای دریافت اطلاعات کانفیگ (وضعیت ' + str(res.status_code) + '):', res.text[:500])
            return None

        data = res.json()

        # اگه پاسخ به شکل {"configs": [ {...} ]} بود، آیتم اول رو بردار
        if isinstance(data, dict) and isinstance(data.get('configs'), list) and data['configs']:
            data = data['configs'][0]

        used_gb = data.get('usedGb')
        if used_gb is not None:
            return float(used_gb)

        # فالبک برای احتمال نام‌گذاری‌های دیگه یا واحد بایت
        used_bytes = (
            data.get('usedTraffic')
            or data.get('used_traffic')
            or data.get('trafficUsed')
            or data.get('usage')
            or data.get('dataUsage')
        )
        if used_bytes is None:
            up = data.get('uploadBytes') or data.get('upload') or 0
            down = data.get('downloadBytes') or data.get('download') or 0
            if up or down:
                used_bytes = up + down

        if used_bytes is None:
            print('⚠️ فیلد مصرف در پاسخ پنل پیدا نشد. پاسخ خام:', json.dumps(data, ensure_ascii=False)[:800])
            return None

        return used_bytes / (1024 ** 3)

    except Exception as e:
        print('⚠️ استثنا در دریافت مصرف کانفیگ:', e)
        return None


@with_db_retry
def delete_config_from_db_by_index(user_id, index):
    configs = get_user_configs(user_id)
    if 0 <= index < len(configs):
        cfg = configs[index]
        delete_config_from_panel(cfg[2])
        conn = get_conn()
        c = conn.cursor()
        c.execute('DELETE FROM user_configs WHERE id=?', (cfg[0],))
        conn.commit()
        conn.close()
        return True
    return False


@with_db_retry
def get_total_purchases(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT SUM(gb), SUM(price) FROM user_configs WHERE user_id=?', (user_id,))
    res = c.fetchone()
    conn.close()
    return (res[0] if res and res[0] else 0, res[1] if res and res[1] else 0)


@with_db_retry
def check_free_trial(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM free_trials WHERE user_id=?', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count


@with_db_retry
def set_free_trial(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO free_trials (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()


@with_db_retry
def create_topup_request(user_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT INTO topup_requests (user_id, amount, created_at) VALUES (?, ?, ?)',
               (user_id, amount, datetime.now().isoformat()))
    conn.commit()
    req_id = c.lastrowid
    conn.close()
    return req_id


@with_db_retry
def get_topup_request(req_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT id, user_id, amount, status FROM topup_requests WHERE id=?', (req_id,))
    res = c.fetchone()
    conn.close()
    return res


@with_db_retry
def update_topup_status(req_id, status):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE topup_requests SET status=? WHERE id=?', (status, req_id))
    conn.commit()
    conn.close()


@with_db_retry
def create_ticket(user_id, message):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT INTO tickets (user_id, message, created_at) VALUES (?, ?, ?)',
               (user_id, message, datetime.now().isoformat()))
    conn.commit()
    tid = c.lastrowid
    conn.close()
    return tid


@with_db_retry
def get_ticket(ticket_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT id, user_id, message, status, admin_reply, created_at FROM tickets WHERE id=?', (ticket_id,))
    res = c.fetchone()
    conn.close()
    return res


@with_db_retry
def update_ticket_status(ticket_id, status, admin_reply=None):
    conn = get_conn()
    c = conn.cursor()
    if admin_reply:
        c.execute('UPDATE tickets SET status=?, admin_reply=? WHERE id=?', (status, admin_reply, ticket_id))
    else:
        c.execute('UPDATE tickets SET status=? WHERE id=?', (status, ticket_id))
    conn.commit()
    conn.close()


@with_db_retry
def get_discount_code(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT code, percent, max_uses, used_count, active FROM discount_codes WHERE code=?',
              (code.strip().upper(),))
    res = c.fetchone()
    conn.close()
    return res


@with_db_retry
def increment_discount_usage(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE discount_codes SET used_count = used_count + 1 WHERE code=?', (code.strip().upper(),))
    conn.commit()
    conn.close()


def validate_discount_code(code):
    """برمی‌گرداند: (ok: bool, percent یا پیام خطا)"""
    row = get_discount_code(code)
    if not row:
        return False, '❌ کد تخفیف نامعتبر است.'
    _, percent, max_uses, used_count, active = row
    if active != 1:
        return False, '⛔ این کد تخفیف غیرفعال شده است.'
    if used_count >= max_uses:
        return False, '⚠️ ظرفیت استفاده از این کد تخفیف تمام شده است.'
    return True, percent


@with_db_retry
def get_setting(key, default='1'):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT value FROM settings WHERE key=?', (key,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else default


def is_feature_enabled(key):
    return get_setting(key, '1') == '1'


DISABLED_TEXT = '⛔ این قابلیت در حال حاضر توسط مدیریت غیرفعال شده است.'


# ---------------------------------------------------------------------------
# Telegram / panel API helpers
# ---------------------------------------------------------------------------

def delete_webhook():
    try:
        res = SESSION.post(BASE_URL + '/deleteWebhook', json={'drop_pending_updates': False}, timeout=10)
        print('🧹 حذف Webhook قبلی:', res.json())
    except Exception as e:
        print('⚠️ خطا در حذف Webhook:', e)


# فهرست دستورهایی که با زدن دکمه «/» توی صفحه کیبورد کاربر نمایش داده می‌شه
BOT_COMMANDS = [
    {'command': 'start', 'description': '🏠 شروع و منوی اصلی'},
    {'command': 'buy', 'description': '🛍 خرید کانفیگ'},
    {'command': 'test', 'description': '🧪 تست رایگان'},
    {'command': 'wallet', 'description': '💠 شارژ کیف پول'},
    {'command': 'myconfigs', 'description': '🗂 کانفیگ‌های من'},
    {'command': 'delconfig', 'description': '🗑 حذف کانفیگ'},
    {'command': 'account', 'description': '👤 حساب من'},
    {'command': 'support', 'description': '🎧 پشتیبانی'},
]

# دستورهای اسلش معادل دکمه‌های منو، به COMMAND_TO_ACTION_KEY (پایین‌تر، کنار
# main_menu) نگاشت می‌شن.


def set_bot_commands():
    try:
        res = SESSION.post(BASE_URL + '/setMyCommands', json={'commands': BOT_COMMANDS}, timeout=10)
        print('📋 تنظیم منوی دستورات:', res.json())
    except Exception as e:
        print('⚠️ خطا در تنظیم منوی دستورات:', e)


def get_updates(offset=None):
    url = BASE_URL + '/getUpdates'
    params = {'timeout': 3}
    if offset:
        params['offset'] = offset
    try:
        res = SESSION.get(url, params=params, timeout=10)
        data = res.json()
        if not data.get('ok'):
            print('⚠️ خطای getUpdates (بات فروش):', data)
        return data
    except Exception as e:
        print('⚠️ استثنا در getUpdates (بات فروش):', e)
        return {'ok': False, 'result': []}


def send_message(chat_id, text, parse_mode='HTML', reply_markup=None):
    url = BASE_URL + '/sendMessage'
    payload = {'chat_id': chat_id, 'text': text}
    if parse_mode:
        payload['parse_mode'] = parse_mode
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        res = SESSION.post(url, json=payload, timeout=15)
        return res.json()
    except Exception:
        return {'ok': False}


def send_photo(chat_id, photo_id, caption=None, parse_mode='HTML', reply_markup=None):
    url = BASE_URL + '/sendPhoto'
    payload = {'chat_id': chat_id, 'photo': photo_id}
    if caption:
        payload['caption'] = caption
    if parse_mode:
        payload['parse_mode'] = parse_mode
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        res = SESSION.post(url, json=payload, timeout=20)
        return res.json()
    except Exception:
        return {'ok': False}


def answer_callback(callback_query_id):
    try:
        SESSION.post(BASE_URL + '/answerCallbackQuery', json={'callback_query_id': callback_query_id}, timeout=5)
    except Exception:
        pass


def make_config(gb, label, days, proto='both'):
    try:
        res = SESSION.post(
            CONFIG_API,
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            json={'gb': gb, 'label': label, 'expiryDays': days, 'proto': proto},
            timeout=20
        )
        if res.status_code in (200, 201):
            data = res.json()
            return {
                'success': True,
                'sub_url': data.get('subUrl'),
                'config_id': data.get('id') or data.get('uuid') or data.get('username'),
                'data': data
            }
        # اگه پنل با proto_unavailable رد کنه (مثلاً فروش OpenVPN موقتاً بسته
        # باشه)، این خطا رو جدا تشخیص می‌دیم تا پیام مناسب به کاربر نشون بدیم.
        err_code = None
        try:
            body = res.json()
            err_code = body.get('error') or body.get('message') or body.get('code')
        except Exception:
            pass
        return {'success': False, 'error': str(res.status_code), 'error_code': err_code}
    except Exception as e:
        return {'success': False, 'error': str(e), 'error_code': None}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# متن‌ها و دکمه‌های قابل‌تغییر از پنل
# ---------------------------------------------------------------------------
# BUTTON_ACTIONS: تعریف هر آیتم منوی اصلی. کلید (اولین عضو) هیچ‌وقت عوض نمی‌شه
# و همیشه تو callback_data (برای حالت اینلاین) استفاده می‌شه؛ چیزی که از پنل
# قابل‌تغییره فقط «متن» دکمه‌ست (get_button_label) نه این کلید داخلی.
def get_bot_text(key, default):
    return get_setting('text_' + key, default)


def get_button_label(key, default):
    return get_setting('btn_' + key, default)


def get_keyboard_style():
    v = get_setting('keyboard_style', 'reply')
    return v if v in ('reply', 'inline') else 'reply'


def action_buy_menu(chat_id):
    if not is_feature_enabled('buy'):
        send_message(chat_id, DISABLED_TEXT)
        return
    wallet = get_wallet_db(chat_id)
    cheapest = min(t['price_per_gb'] for t in get_config_types().values())
    if wallet < cheapest:
        send_message(chat_id, '⚠️ موجودی کافی نیست.\n💳 موجودی فعلی: <b>' + f'{wallet:,}' + '</b> تومان')
        return
    user_steps[str(chat_id)] = {}
    type_kb = {
        'inline_keyboard': [
            [{'text': get_config_types()['wireguard']['label'], 'callback_data': 'buytype_wireguard'}],
            [{'text': get_config_types()['config']['label'], 'callback_data': 'buytype_config'}],
            [{'text': get_config_types()['both']['label'], 'callback_data': 'buytype_both'}],
            [{'text': get_config_types()['openvpn']['label'], 'callback_data': 'buytype_openvpn'}],
        ]
    }
    send_message(
        chat_id,
        '🛍 نوع سرویس مورد نظر را انتخاب کنید:\n' + DIVIDER + '\n'
        '🔒 فقط وایرگارد: <b>' + f"{get_config_types()['wireguard']['price_per_gb']:,}" + '</b> تومان/گیگ\n'
        '⚙️ فقط کانفیگ: <b>' + f"{get_config_types()['config']['price_per_gb']:,}" + '</b> تومان/گیگ\n'
        '🔀 هر دو: <b>' + f"{get_config_types()['both']['price_per_gb']:,}" + '</b> تومان/گیگ\n'
        '📱 فقط OpenVPN: <b>' + f"{get_config_types()['openvpn']['price_per_gb']:,}" + '</b> تومان/گیگ\n\n'
        '💳 موجودی فعلی: <b>' + f'{wallet:,}' + '</b> تومان',
        reply_markup=type_kb
    )


def action_topup_menu(chat_id):
    if not is_feature_enabled('topup'):
        send_message(chat_id, DISABLED_TEXT)
        return
    user_steps[str(chat_id)] = {'step': 'ask_topup_amount'}
    send_message(
        chat_id,
        get_bot_text('topup_prompt', '💠 مبلغ مورد نظر برای شارژ کیف پول را به تومان وارد کنید.') + '\n'
        'حداقل مبلغ شارژ: <b>' + f'{get_min_topup():,}' + '</b> تومان'
    )


def action_trial(chat_id):
    if not is_feature_enabled('trial'):
        send_message(chat_id, DISABLED_TEXT)
        return
    if check_free_trial(chat_id) > 0:
        send_message(chat_id, '⚠️ شما پیش‌تر از تست رایگان استفاده کرده‌اید.')
        return
    send_message(chat_id, '⏳ در حال ساخت کانفیگ تست...')
    res = make_config(0.1, 'تست رایگان', 1)
    if res['success'] and res['sub_url']:
        set_free_trial(chat_id)
        save_user_config(chat_id, res['sub_url'], res['config_id'], 0.1, 'تست رایگان', 1, 0)
        trial_text = (
            '🎁 <b>کانفیگ تست رایگان شما آماده شد</b>\n' + DIVIDER + '\n'
            '📶 حجم: <b>0.1</b> گیگابایت\n'
            '⏳ مدت اعتبار: <b>1</b> روز\n' + DIVIDER + '\n'
            '🔗 لینک سابسکریپشن:\n<code>' + str(res['sub_url']) + '</code>'
        )
        send_message(chat_id, trial_text)
    else:
        send_message(chat_id, '❌ خطا در ساخت کانفیگ تست. لطفاً بعداً دوباره تلاش کنید.')


def action_account(chat_id):
    wallet = get_wallet_db(chat_id)
    tg, tp = get_total_purchases(chat_id)
    profile_text = (
        '👤 <b>حساب کاربری شما</b>\n' + DIVIDER + '\n'
        '🆔 شناسه: <code>' + str(chat_id) + '</code>\n'
        '💳 موجودی کیف پول: <b>' + f'{wallet:,}' + '</b> تومان\n'
        '📊 مجموع خرید: <b>' + f'{tg:,.1f}' + '</b> گیگابایت\n'
        '💵 مجموع پرداختی: <b>' + f'{tp:,.0f}' + '</b> تومان'
    )
    send_message(chat_id, profile_text)


def action_my_configs(chat_id):
    configs = get_user_configs(chat_id)
    if not configs:
        send_message(chat_id, '📭 هنوز هیچ کانفیگی ثبت نکرده‌اید.')
        return
    txt = '🗂 <b>لیست کانفیگ‌های شما</b>\n' + DIVIDER + '\n\n'
    manage_rows = []
    for i, cfg in enumerate(configs, 1):
        ctype_label = get_config_types().get(cfg[9], {}).get('label', '') if len(cfg) > 9 else ''
        status = cfg[10] if len(cfg) > 10 else 'active'
        status_badge = ' — ⏸ متوقف' if status == 'paused' else ''
        txt += (
            '<b>' + str(i) + '.</b> ' + str(cfg[4]) + ' — ' + str(cfg[3]) +
            'GB — ' + str(cfg[5]) + ' روز' +
            (' — ' + ctype_label if ctype_label else '') +
            status_badge +
            '\n<code>' + str(cfg[1]) + '</code>\n\n'
        )
        btn_label = str(cfg[4])[:20]
        manage_rows.append([{
            'text': '⚙️ مدیریت #' + str(i) + ' — ' + btn_label,
            'callback_data': 'managecfg_' + str(cfg[0])
        }])
    send_message(chat_id, txt, reply_markup={'inline_keyboard': manage_rows})


def action_delete_config_start(chat_id):
    if not is_feature_enabled('delete_config'):
        send_message(chat_id, DISABLED_TEXT)
        return
    configs = get_user_configs(chat_id)
    if not configs:
        send_message(chat_id, '📭 کانفیگی برای حذف وجود ندارد.')
        return
    txt = '🗑 شماره ردیف کانفیگ مورد نظر برای حذف را ارسال کنید:\n\n'
    for i, cfg in enumerate(configs, 1):
        txt += '<b>' + str(i) + '.</b> ' + str(cfg[4]) + ' (' + str(cfg[3]) + 'GB)\n'
    send_message(chat_id, txt)
    user_steps[str(chat_id)] = {'step': 'waiting_delete_id'}


def action_support_start(chat_id):
    if not is_feature_enabled('support'):
        send_message(chat_id, DISABLED_TEXT)
        return
    user_steps[str(chat_id)] = {'step': 'support_message'}
    send_message(chat_id, get_bot_text('support_prompt', '🎧 پیام خود را برای پشتیبانی ارسال کنید:'))


# هر آیتم: (کلید داخلی ثابت, متن پیش‌فرض دکمه, تابع اجراکننده)
BUTTON_ACTIONS = [
    ('buy', '🛍 خرید کانفیگ', action_buy_menu),
    ('trial', '🧪 تست رایگان', action_trial),
    ('topup', '💠 شارژ کیف پول', action_topup_menu),
    ('account', '👤 حساب من', action_account),
    ('myconfigs', '🗂 کانفیگ‌های من', action_my_configs),
    ('delete', '🗑 حذف کانفیگ', action_delete_config_start),
    ('support', '🎧 پشتیبانی', action_support_start),
]

# دستورهای اسلش معادل همون آیتم‌های منو (به کلید داخلی نگاشت می‌شن، نه متن دکمه؛
# این‌طوری حتی اگه متن دکمه از پنل عوض بشه، دستورها درست کار می‌کنن)
COMMAND_TO_ACTION_KEY = {
    '/buy': 'buy',
    '/test': 'trial',
    '/wallet': 'topup',
    '/myconfigs': 'myconfigs',
    '/delconfig': 'delete',
    '/account': 'account',
    '/support': 'support',
}


def get_action_handlers():
    return {key: fn for key, _label, fn in BUTTON_ACTIONS}


def get_label_to_action_key():
    return {get_button_label(key, default_label): key for key, default_label, _fn in BUTTON_ACTIONS}


def main_menu(chat_id, note=None):
    wallet = get_wallet_db(chat_id)
    labels = {key: get_button_label(key, default_label) for key, default_label, _fn in BUTTON_ACTIONS}

    if get_keyboard_style() == 'inline':
        keyboard = {
            'inline_keyboard': [
                [{'text': labels['buy'], 'callback_data': 'menu_buy'}, {'text': labels['trial'], 'callback_data': 'menu_trial'}],
                [{'text': labels['topup'], 'callback_data': 'menu_topup'}],
                [{'text': labels['account'], 'callback_data': 'menu_account'}],
                [{'text': labels['myconfigs'], 'callback_data': 'menu_myconfigs'}, {'text': labels['delete'], 'callback_data': 'menu_delete'}],
                [{'text': labels['support'], 'callback_data': 'menu_support'}],
            ]
        }
    else:
        keyboard = {
            'keyboard': [
                [{'text': labels['buy']}, {'text': labels['trial']}],
                [{'text': labels['topup']}],
                [{'text': labels['account']}],
                [{'text': labels['myconfigs']}, {'text': labels['delete']}],
                [{'text': labels['support']}],
            ],
            'resize_keyboard': True
        }

    text = (
        '✨ <b>' + get_bot_text('welcome_intro', 'به ربات فروش خوش آمدید') + '</b>\n' + DIVIDER + '\n'
        '🔒 فقط وایرگارد: <b>' + f"{get_config_types()['wireguard']['price_per_gb']:,}" + '</b> تومان/گیگ\n'
        '⚙️ فقط کانفیگ: <b>' + f"{get_config_types()['config']['price_per_gb']:,}" + '</b> تومان/گیگ\n'
        '🔀 هر دو: <b>' + f"{get_config_types()['both']['price_per_gb']:,}" + '</b> تومان/گیگ\n'
        '📱 فقط OpenVPN: <b>' + f"{get_config_types()['openvpn']['price_per_gb']:,}" + '</b> تومان/گیگ\n'
        '💳 موجودی کیف پول: <b>' + f'{wallet:,}' + '</b> تومان'
    )
    if note:
        text = note + '\n' + DIVIDER + '\n' + text
    send_message(chat_id, text, reply_markup=keyboard)


def notify_admin_new_ticket(tid, user_id, message):
    kb = {
        'inline_keyboard': [[
            {'text': '✍️ پاسخ', 'callback_data': 'reply_ticket_' + str(tid)},
            {'text': '🔒 بستن', 'callback_data': 'close_ticket_' + str(tid)}
        ]]
    }
    txt = (
        '🎫 <b>تیکت جدید #' + str(tid) + '</b>\n' + DIVIDER + '\n'
        '👤 کاربر: <code>' + str(user_id) + '</code>\n\n' + str(message)
    )
    send_message(ADMIN_ID, txt, reply_markup=kb)


# ---------------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------------

def network_diagnostic():
    for name, url in [('api.splus.ir', 'https://api.splus.ir'), ('api.telegram.org', 'https://api.telegram.org')]:
        try:
            r = SESSION.get(url, timeout=8)
            print(f'🔎 تست اتصال به {name}: موفق (کد {r.status_code})')
        except Exception as e:
            print(f'🔎 تست اتصال به {name}: ناموفق ({e})')


init_db()
network_diagnostic()
delete_webhook()
set_bot_commands()
print('✅ ربات با موفقیت اجرا شد.')
last_update_id = 0

while True:
    try:
        updates = get_updates(last_update_id + 1)

        if updates.get('ok') and updates.get('result'):
            for update in updates['result']:
                last_update_id = update['update_id']

                # ------------------------------------------------------------------
                # Callback queries (inline button presses)
                # ------------------------------------------------------------------
                if 'callback_query' in update:
                    query = update['callback_query']
                    chat_id = query['message']['chat']['id']
                    data = query.get('data', '')
                    answer_callback(query['id'])

                    if data.startswith('menu_'):
                        action_key = data.split('_', 1)[1]
                        handler = get_action_handlers().get(action_key)
                        if handler:
                            handler(chat_id)

                    elif data == 'confirm_purchase':
                        s = user_steps.get(str(chat_id), {})
                        if s.get('step') == 'confirm_buy':
                            gb, label, days, price = s['gb'], s['label'], s['days'], s['price']
                            ctype = s.get('type', 'both')
                            cfg_type = get_config_types().get(ctype, get_config_types()['both'])
                            if get_wallet_db(chat_id) < price:
                                send_message(chat_id, '⚠️ موجودی کیف پول کافی نیست.')
                            else:
                                send_message(chat_id, '⏳ در حال ساخت کانفیگ، لطفاً شکیبا باشید...')
                                res = make_config(gb, label, days, proto=cfg_type['proto'])
                                if res['success'] and res['sub_url']:
                                    update_wallet_db(chat_id, -price)
                                    save_user_config(chat_id, res['sub_url'], res['config_id'], gb, label, days, price, ctype)
                                    if s.get('discount_code'):
                                        increment_discount_usage(s['discount_code'])
                                    if ctype == 'wireguard':
                                        output_label = '📄 کانفیگ وایرگارد:'
                                    elif ctype == 'openvpn':
                                        output_label = '📄 فایل OpenVPN:'
                                    else:
                                        output_label = '🔗 لینک سابسکریپشن:'
                                    success_text = (
                                        '🎉 <b>کانفیگ شما با موفقیت ساخته شد</b>\n' + DIVIDER + '\n'
                                        '📦 نوع سرویس: <b>' + cfg_type['label'] + '</b>\n'
                                        '📶 حجم: <b>' + str(gb) + '</b> گیگابایت\n'
                                        '🏷 نام: <b>' + str(label) + '</b>\n'
                                        '⏳ مدت اعتبار: <b>' + str(days) + '</b> روز\n'
                                        '💵 قیمت: <b>' + f'{price:,}' + '</b> تومان\n' + DIVIDER + '\n'
                                        + output_label + '\n<code>' + str(res['sub_url']) + '</code>'
                                    )
                                    send_message(chat_id, success_text)
                                elif res.get('error_code') and 'proto_unavailable' in str(res.get('error_code')).lower():
                                    send_message(
                                        chat_id,
                                        '⛔ فروش سرویس «' + cfg_type['label'] + '» در حال حاضر توسط پنل بسته است.\n'
                                        'لطفاً نوع سرویس دیگری را انتخاب کنید یا بعداً دوباره تلاش کنید.\n'
                                        '(مبلغی از کیف پول شما کسر نشد.)'
                                    )
                                else:
                                    send_message(chat_id, '❌ خطا در ارتباط با سرور پنل. لطفاً دوباره تلاش کنید.')
                            user_steps[str(chat_id)] = {}
                            main_menu(chat_id)
                        else:
                            main_menu(chat_id)

                    elif data == 'skip_discount':
                        s = user_steps.get(str(chat_id), {})
                        if s.get('step') == 'ask_discount':
                            gb, days, label, price = s['gb'], s['days'], s['label'], s['price']
                            ctype = s.get('type', 'both')
                            user_steps[str(chat_id)] = {
                                'step': 'confirm_buy',
                                'gb': gb,
                                'days': days,
                                'price': price,
                                'label': label,
                                'type': ctype
                            }
                            confirm_text = (
                                '🧾 <b>تایید نهایی خرید</b>\n' + DIVIDER + '\n'
                                '📦 نوع سرویس: <b>' + get_config_types().get(ctype, get_config_types()['both'])['label'] + '</b>\n'
                                '📶 حجم: <b>' + str(gb) + '</b> گیگابایت\n'
                                '🏷 نام: <b>' + str(label) + '</b>\n'
                                '⏳ مدت اعتبار: <b>' + str(days) + '</b> روز\n'
                                '💵 قیمت: <b>' + f'{price:,}' + '</b> تومان'
                            )
                            confirm_kb = {
                                'inline_keyboard': [[
                                    {'text': '✅ تایید و پرداخت', 'callback_data': 'confirm_purchase'},
                                    {'text': '❌ انصراف', 'callback_data': 'cancel_purchase'}
                                ]]
                            }
                            send_message(chat_id, confirm_text, reply_markup=confirm_kb)
                        else:
                            main_menu(chat_id)

                    elif data == 'cancel_purchase' or data == 'menu':
                        user_steps[str(chat_id)] = {}
                        main_menu(chat_id)

                    elif data == 'confdel_yes':
                        s = user_steps.get(str(chat_id), {})
                        if s.get('step') == 'confirm_delete':
                            idx = s['index']
                            refund = s['refund']
                            if delete_config_from_db_by_index(chat_id, idx):
                                if refund > 0:
                                    update_wallet_db(chat_id, refund)
                                    send_message(chat_id, '✅ کانفیگ حذف شد و مبلغ <b>' + f'{refund:,}' + '</b> تومان به کیف پول شما اضافه شد.')
                                else:
                                    send_message(chat_id, '✅ کانفیگ حذف شد.')
                            else:
                                send_message(chat_id, '⚠️ خطا در حذف کانفیگ. لطفاً دوباره تلاش کنید.')
                        user_steps[str(chat_id)] = {}
                        main_menu(chat_id)

                    elif data == 'confdel_no':
                        user_steps[str(chat_id)] = {}
                        send_message(chat_id, '❌ عملیات حذف لغو شد.')
                        main_menu(chat_id)

                    # ---------------- مدیریت کانفیگ (تمدید/تغییر نام/توقف/ازسرگیری/تغییر لینک) ----------------

                    elif data.startswith('managecfg_'):
                        row_id = int(data.split('_', 1)[1])
                        cfg = get_config_row(chat_id, row_id)
                        if not cfg:
                            send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                        else:
                            manage_kb = {
                                'inline_keyboard': [
                                    [{'text': '➕ تمدید (حجم/روز)', 'callback_data': 'cfgext_' + str(row_id)}],
                                    [{'text': '✏️ تغییر نام', 'callback_data': 'cfgrename_' + str(row_id)}],
                                    [{'text': '⏸ توقف', 'callback_data': 'cfgpause_' + str(row_id)}],
                                    [{'text': '▶️ از سرگیری', 'callback_data': 'cfgresume_' + str(row_id)}],
                                    [{'text': '🔄 تغییر لینک', 'callback_data': 'cfgrotate_' + str(row_id)}],
                                    [{'text': '❌ بستن', 'callback_data': 'menu'}],
                                ]
                            }
                            send_message(
                                chat_id,
                                '⚙️ <b>مدیریت کانفیگ</b>\n' + DIVIDER + '\n'
                                '🏷 نام: <b>' + str(cfg[4]) + '</b>\n'
                                '📶 حجم: <b>' + str(cfg[3]) + '</b> گیگابایت\n'
                                '⏳ مدت: <b>' + str(cfg[5]) + '</b> روز\n\n'
                                'یکی از عملیات زیر را انتخاب کنید:',
                                reply_markup=manage_kb
                            )

                    elif data.startswith('cfgext_'):
                        row_id = int(data.split('_', 1)[1])
                        cfg = get_config_row(chat_id, row_id)
                        if not cfg:
                            send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                        else:
                            user_steps[str(chat_id)] = {'step': 'ext_ask_gb', 'row_id': row_id}
                            send_message(chat_id, '➕ چند گیگابایت به این کانفیگ اضافه شود؟ (فقط عدد ارسال کنید)')

                    elif data.startswith('cfgrename_'):
                        row_id = int(data.split('_', 1)[1])
                        cfg = get_config_row(chat_id, row_id)
                        if not cfg:
                            send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                        else:
                            user_steps[str(chat_id)] = {'step': 'rename_ask_label', 'row_id': row_id}
                            send_message(chat_id, '✏️ نام جدید کانفیگ را ارسال کنید:')

                    elif data.startswith('cfgpause_'):
                        row_id = int(data.split('_', 1)[1])
                        cfg = get_config_row(chat_id, row_id)
                        if not cfg:
                            send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                        else:
                            res = pause_config_on_panel(cfg[2])
                            if res['success']:
                                update_config_status_db(row_id, 'paused')
                                send_message(chat_id, '⏸ کانفیگ «' + str(cfg[4]) + '» متوقف شد.')
                            else:
                                send_message(chat_id, '❌ خطا در توقف کانفیگ. لطفاً بعداً دوباره تلاش کنید.')

                    elif data.startswith('cfgresume_'):
                        row_id = int(data.split('_', 1)[1])
                        cfg = get_config_row(chat_id, row_id)
                        if not cfg:
                            send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                        else:
                            res = resume_config_on_panel(cfg[2])
                            if res['success']:
                                update_config_status_db(row_id, 'active')
                                send_message(chat_id, '▶️ کانفیگ «' + str(cfg[4]) + '» از سر گرفته شد.')
                            else:
                                send_message(chat_id, '❌ خطا در از سرگیری کانفیگ. لطفاً بعداً دوباره تلاش کنید.')

                    elif data.startswith('cfgrotate_'):
                        row_id = int(data.split('_', 1)[1])
                        cfg = get_config_row(chat_id, row_id)
                        if not cfg:
                            send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                        else:
                            res = rotate_link_config_on_panel(cfg[2])
                            if res['success']:
                                resp = res.get('data') or {}
                                new_url = None
                                if isinstance(resp, dict):
                                    new_url = resp.get('sub_url') or resp.get('subUrl') or resp.get('url')
                                if new_url:
                                    update_config_suburl_db(row_id, new_url)
                                    send_message(
                                        chat_id,
                                        '🔄 لینک کانفیگ «' + str(cfg[4]) + '» تغییر کرد:\n<code>' + str(new_url) + '</code>'
                                    )
                                else:
                                    send_message(chat_id, '🔄 لینک تغییر کرد؛ برای دیدن لینک جدید، دوباره «کانفیگ‌های من» را بزنید.')
                            else:
                                send_message(chat_id, '❌ خطا در تغییر لینک. لطفاً بعداً دوباره تلاش کنید.')

                    elif data == 'extconfirm_yes':
                        s = user_steps.get(str(chat_id), {})
                        if s.get('step') == 'ext_confirm':
                            row_id = s['row_id']
                            extra_gb = s['extra_gb']
                            extra_days = s['extra_days']
                            price = s['price']
                            cfg = get_config_row(chat_id, row_id)
                            if not cfg:
                                send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                            elif get_wallet_db(chat_id) < price:
                                send_message(chat_id, '⚠️ موجودی کیف پول کافی نیست.')
                            else:
                                res = extend_config_on_panel(cfg[2], extra_gb, extra_days)
                                if res['success']:
                                    extend_config_db(row_id, extra_gb, extra_days)
                                    update_wallet_db(chat_id, -price)
                                    send_message(
                                        chat_id,
                                        '✅ کانفیگ «' + str(cfg[4]) + '» تمدید شد: +' + str(extra_gb) +
                                        ' گیگابایت، +' + str(extra_days) + ' روز — <b>' + f'{price:,}' + '</b> تومان کسر شد.'
                                    )
                                else:
                                    send_message(chat_id, '❌ خطا در تمدید کانفیگ از سمت پنل. لطفاً بعداً دوباره تلاش کنید.')
                        user_steps[str(chat_id)] = {}
                        main_menu(chat_id)

                    elif data == 'extconfirm_no':
                        user_steps[str(chat_id)] = {}
                        send_message(chat_id, '❌ تمدید لغو شد.')
                        main_menu(chat_id)

                    elif data.startswith('buytype_'):
                        ctype = data.split('_', 1)[1]
                        cfg_type = get_config_types().get(ctype)
                        if not cfg_type:
                            main_menu(chat_id)
                        else:
                            wallet = get_wallet_db(chat_id)
                            user_steps[str(chat_id)] = {'step': 'ask_gb', 'type': ctype}
                            send_message(
                                chat_id,
                                '📶 چند گیگابایت نیاز دارید؟\n(' + cfg_type['label'] + ' — هر گیگابایت <b>' +
                                f"{cfg_type['price_per_gb']:,}" + '</b> تومان)\n'
                                '💳 موجودی فعلی: <b>' + f'{wallet:,}' + '</b> تومان\n\n'
                                'فقط عدد حجم را ارسال کنید:'
                            )

                    elif data.startswith('reply_ticket_'):
                        tid = int(data.split('_')[2])
                        user_steps[str(chat_id)] = {'step': 'admin_reply', 'ticket_id': tid}
                        send_message(
                            chat_id,
                            '✍️ پاسخ تیکت #' + str(tid) + ' را ارسال کنید:',
                            reply_markup={'inline_keyboard': [[{'text': '❌ انصراف', 'callback_data': 'menu'}]]}
                        )

                    elif data.startswith('close_ticket_'):
                        tid = int(data.split('_')[2])
                        update_ticket_status(tid, 'closed')
                        send_message(chat_id, '🔒 تیکت #' + str(tid) + ' بسته شد.')

                    elif data.startswith('topup_acc_'):
                        req_id = int(data.split('_')[2])
                        req = get_topup_request(req_id)
                        if req and req[3] == 'pending':
                            update_topup_status(req_id, 'approved')
                            update_wallet_db(req[1], req[2])
                            send_message(req[1], '✅ کیف پول شما به مبلغ <b>' + f'{req[2]:,}' + '</b> تومان شارژ شد.')
                            send_message(ADMIN_ID, '✅ درخواست شارژ #' + str(req_id) + ' تایید و اعمال شد.')
                        else:
                            send_message(ADMIN_ID, '⚠️ این درخواست قبلاً بررسی شده یا وجود ندارد.')

                    elif data.startswith('topup_rej_'):
                        req_id = int(data.split('_')[2])
                        req = get_topup_request(req_id)
                        if req and req[3] == 'pending':
                            update_topup_status(req_id, 'rejected')
                            send_message(req[1], '❌ رسید واریز شما رد شد. لطفاً با پشتیبانی در ارتباط باشید.')
                            send_message(ADMIN_ID, '❌ درخواست #' + str(req_id) + ' رد شد.')
                        else:
                            send_message(ADMIN_ID, '⚠️ این درخواست قبلاً بررسی شده یا وجود ندارد.')

                # ------------------------------------------------------------------
                # Regular messages
                # ------------------------------------------------------------------
                elif 'message' in update:
                    message = update['message']
                    chat_id = message['chat']['id']
                    text = message.get('text', '')
                    step = user_steps.get(str(chat_id), {})

                    sender = message.get('from', {}) or {}
                    upsert_user_info(chat_id, sender.get('username'), sender.get('first_name'))

                    matched_action_key = COMMAND_TO_ACTION_KEY.get(text) or get_label_to_action_key().get(text)

                    if text == '/start':
                        add_user(chat_id)
                        user_steps[str(chat_id)] = {}
                        main_menu(chat_id)

                    elif matched_action_key:
                        get_action_handlers()[matched_action_key](chat_id)

                    # ---------------- step-based flows ----------------

                    elif step.get('step') == 'ask_gb':
                        try:
                            gb = int(text)
                            if gb <= 0:
                                send_message(chat_id, '⚠️ عددی بزرگ‌تر از صفر وارد کنید.')
                            else:
                                ctype = step.get('type', 'both')
                                cfg_type = get_config_types().get(ctype, get_config_types()['both'])
                                price = gb * cfg_type['price_per_gb']
                                wallet = get_wallet_db(chat_id)
                                if wallet < price:
                                    send_message(chat_id, '⚠️ موجودی کیف پول کافی نیست. لطفاً ابتدا کیف پول خود را شارژ کنید.')
                                    user_steps[str(chat_id)] = {}
                                else:
                                    user_steps[str(chat_id)] = {'step': 'ask_days', 'gb': gb, 'price': price, 'type': ctype}
                                    send_message(chat_id, '⏳ مدت اعتبار کانفیگ چند روز باشد؟ (فقط عدد روز را ارسال کنید)')
                        except ValueError:
                            send_message(chat_id, '⚠️ لطفاً فقط عدد وارد کنید.')

                    elif step.get('step') == 'ask_days':
                        try:
                            days = int(text)
                            if days <= 0:
                                send_message(chat_id, '⚠️ عددی بزرگ‌تر از صفر وارد کنید.')
                            else:
                                user_steps[str(chat_id)]['step'] = 'ask_label'
                                user_steps[str(chat_id)]['days'] = days
                                send_message(chat_id, '🏷 یک نام دلخواه برای این کانفیگ ارسال کنید:')
                        except ValueError:
                            send_message(chat_id, '⚠️ لطفاً فقط عدد وارد کنید.')

                    elif step.get('step') == 'ask_label':
                        label = text.strip() if text.strip() else ('کاربر-' + str(chat_id))
                        gb = step['gb']
                        days = step['days']
                        price = step['price']
                        ctype = step.get('type', 'both')
                        user_steps[str(chat_id)] = {
                            'step': 'ask_discount',
                            'gb': gb,
                            'days': days,
                            'price': price,
                            'label': label,
                            'type': ctype
                        }
                        discount_kb = {
                            'inline_keyboard': [[
                                {'text': '➡️ ادامه بدون کد تخفیف', 'callback_data': 'skip_discount'}
                            ]]
                        }
                        send_message(
                            chat_id,
                            '🎟 اگر کد تخفیف دارید ارسال کنید، در غیر این صورت روی دکمه‌ی زیر بزنید:',
                            reply_markup=discount_kb
                        )

                    elif step.get('step') == 'ask_discount':
                        gb = step['gb']
                        days = step['days']
                        label = step['label']
                        original_price = step['price']
                        ctype = step.get('type', 'both')
                        ok, result = validate_discount_code(text)
                        if not ok:
                            send_message(chat_id, result)
                        else:
                            percent = result
                            final_price = round(original_price * (100 - percent) / 100)
                            user_steps[str(chat_id)] = {
                                'step': 'confirm_buy',
                                'gb': gb,
                                'days': days,
                                'price': final_price,
                                'label': label,
                                'type': ctype,
                                'discount_code': text.strip().upper(),
                                'discount_percent': percent,
                                'original_price': original_price
                            }
                            confirm_text = (
                                '🧾 <b>تایید نهایی خرید</b>\n' + DIVIDER + '\n'
                                '📦 نوع سرویس: <b>' + get_config_types().get(ctype, get_config_types()['both'])['label'] + '</b>\n'
                                '📶 حجم: <b>' + str(gb) + '</b> گیگابایت\n'
                                '🏷 نام: <b>' + str(label) + '</b>\n'
                                '⏳ مدت اعتبار: <b>' + str(days) + '</b> روز\n'
                                '🎟 کد تخفیف: <b>' + text.strip().upper() + '</b> (' + str(percent) + '٪)\n'
                                '💵 قیمت قبل از تخفیف: <s>' + f'{original_price:,}' + '</s> تومان\n'
                                '💰 قیمت نهایی: <b>' + f'{final_price:,}' + '</b> تومان'
                            )
                            confirm_kb = {
                                'inline_keyboard': [[
                                    {'text': '✅ تایید و پرداخت', 'callback_data': 'confirm_purchase'},
                                    {'text': '❌ انصراف', 'callback_data': 'cancel_purchase'}
                                ]]
                            }
                            send_message(chat_id, confirm_text, reply_markup=confirm_kb)

                    elif step.get('step') == 'ext_ask_gb':
                        try:
                            extra_gb = float(text)
                            if extra_gb <= 0:
                                send_message(chat_id, '⚠️ عددی بزرگ‌تر از صفر وارد کنید.')
                            else:
                                user_steps[str(chat_id)]['step'] = 'ext_ask_days'
                                user_steps[str(chat_id)]['extra_gb'] = extra_gb
                                send_message(chat_id, '⏳ چند روز به مدت اعتبار اضافه شود؟ (فقط عدد ارسال کنید)')
                        except ValueError:
                            send_message(chat_id, '⚠️ لطفاً فقط عدد وارد کنید.')

                    elif step.get('step') == 'ext_ask_days':
                        try:
                            extra_days = int(text)
                            if extra_days <= 0:
                                send_message(chat_id, '⚠️ عددی بزرگ‌تر از صفر وارد کنید.')
                            else:
                                row_id = step['row_id']
                                extra_gb = step['extra_gb']
                                cfg = get_config_row(chat_id, row_id)
                                if not cfg:
                                    send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                                    user_steps[str(chat_id)] = {}
                                else:
                                    ctype = cfg[9] if len(cfg) > 9 else 'both'
                                    price_per_gb = get_config_types().get(ctype, get_config_types()['both'])['price_per_gb']
                                    price = round(extra_gb * price_per_gb)
                                    user_steps[str(chat_id)] = {
                                        'step': 'ext_confirm',
                                        'row_id': row_id,
                                        'extra_gb': extra_gb,
                                        'extra_days': extra_days,
                                        'price': price
                                    }
                                    confirm_text = (
                                        '🧾 <b>تایید تمدید کانفیگ</b>\n' + DIVIDER + '\n'
                                        '🏷 نام: <b>' + str(cfg[4]) + '</b>\n'
                                        '➕ افزایش حجم: <b>' + str(extra_gb) + '</b> گیگابایت\n'
                                        '⏳ افزایش مدت: <b>' + str(extra_days) + '</b> روز\n'
                                        '💵 هزینه: <b>' + f'{price:,}' + '</b> تومان'
                                    )
                                    confirm_kb = {
                                        'inline_keyboard': [[
                                            {'text': '✅ تایید و پرداخت', 'callback_data': 'extconfirm_yes'},
                                            {'text': '❌ انصراف', 'callback_data': 'extconfirm_no'}
                                        ]]
                                    }
                                    send_message(chat_id, confirm_text, reply_markup=confirm_kb)
                        except ValueError:
                            send_message(chat_id, '⚠️ لطفاً فقط عدد وارد کنید.')

                    elif step.get('step') == 'rename_ask_label':
                        new_label = text.strip()
                        row_id = step.get('row_id')
                        if not new_label:
                            send_message(chat_id, '⚠️ نام نمی‌تواند خالی باشد.')
                        else:
                            cfg = get_config_row(chat_id, row_id)
                            if not cfg:
                                send_message(chat_id, '⚠️ این کانفیگ یافت نشد.')
                            else:
                                res = rename_config_on_panel(cfg[2], new_label)
                                if res['success']:
                                    update_config_label_db(row_id, new_label)
                                    # فقط تغییر لیبل کافی نیست: خودِ فایل/ساب‌لینک قبلاً
                                    # با نام قدیمی روی پنل ساخته و کش شده، برای همین بعد
                                    # از تغییر نام، لینک رو هم رفرش (rotate) می‌کنیم تا
                                    # نام جدید واقعاً داخل ساب‌لینک/فایل دیده بشه.
                                    rotate_res = rotate_link_config_on_panel(cfg[2])
                                    new_url = None
                                    if rotate_res.get('success'):
                                        resp = rotate_res.get('data') or {}
                                        if isinstance(resp, dict):
                                            new_url = resp.get('sub_url') or resp.get('subUrl') or resp.get('url')
                                        if new_url:
                                            update_config_suburl_db(row_id, new_url)
                                    if new_url:
                                        send_message(
                                            chat_id,
                                            '✅ نام کانفیگ به «' + new_label + '» تغییر کرد.\n'
                                            '🔗 لینک به‌روزشده:\n<code>' + str(new_url) + '</code>\n\n'
                                            '⚠️ لطفاً این لینک جدید را دوباره در برنامه‌ی خود وارد کنید.'
                                        )
                                    else:
                                        send_message(
                                            chat_id,
                                            '✅ نام کانفیگ به «' + new_label + '» تغییر کرد.\n'
                                            'برای دیدن لینک فعلی، دوباره «🗂 کانفیگ‌های من» را بزنید.'
                                        )
                                else:
                                    send_message(chat_id, '❌ خطا در تغییر نام از سمت پنل. لطفاً بعداً دوباره تلاش کنید.')
                            user_steps[str(chat_id)] = {}
                            main_menu(chat_id)

                    elif step.get('step') == 'waiting_delete_id':
                        try:
                            idx = int(text) - 1
                            configs = get_user_configs(chat_id)
                            if not (0 <= idx < len(configs)):
                                send_message(chat_id, '⚠️ شماره ردیف نامعتبر است.')
                                user_steps[str(chat_id)] = {}
                            else:
                                cfg = configs[idx]
                                gb, price, config_id, label = cfg[3], cfg[6], cfg[2], cfg[4]
                                price_per_gb = (price / gb) if gb else 0

                                used_gb = get_config_usage_gb(config_id)
                                if used_gb is None:
                                    used_gb = 0
                                    refund = 0
                                    usage_note = '⚠️ مصرف این کانفیگ از پنل قابل بررسی نبود؛ برای احتیاط مبلغی بازگردانده نمی‌شود.'
                                else:
                                    used_gb = min(used_gb, gb)
                                    used_value = used_gb * price_per_gb
                                    remaining_value = price - used_value
                                    refund = int(remaining_value / 2)
                                    usage_note = ''

                                confirm_text = (
                                    '🗑 <b>تایید حذف کانفیگ</b>\n' + DIVIDER + '\n'
                                    '🏷 نام: <b>' + str(label) + '</b>\n'
                                    '📶 حجم خریداری‌شده: <b>' + str(gb) + '</b> گیگابایت\n'
                                    '📊 مصرف‌شده: <b>' + f'{used_gb:.2f}' + '</b> گیگابایت\n'
                                    '💵 قیمت خرید: <b>' + f'{price:,}' + '</b> تومان\n' + DIVIDER + '\n'
                                    '💰 مبلغی که به کیف پولتان بازمی‌گردد (نصف ارزش باقیمانده): <b>' + f'{refund:,}' + '</b> تومان\n\n'
                                    + (usage_note + '\n\n' if usage_note else '')
                                    + '⚠️ این عملیات غیرقابل بازگشت است. مطمئنید می‌خواهید این کانفیگ حذف شود؟'
                                )
                                confirm_kb = {
                                    'inline_keyboard': [[
                                        {'text': '✅ بله، حذف کن', 'callback_data': 'confdel_yes'},
                                        {'text': '❌ انصراف', 'callback_data': 'confdel_no'}
                                    ]]
                                }
                                user_steps[str(chat_id)] = {'step': 'confirm_delete', 'index': idx, 'refund': refund}
                                send_message(chat_id, confirm_text, reply_markup=confirm_kb)
                        except ValueError:
                            send_message(chat_id, '⚠️ لطفاً فقط شماره ردیف را وارد کنید.')
                            user_steps[str(chat_id)] = {}

                    elif step.get('step') == 'ask_topup_amount':
                        try:
                            amount = int(text)
                            if amount < get_min_topup():
                                send_message(chat_id, '⚠️ حداقل مبلغ شارژ <b>' + f'{get_min_topup():,}' + '</b> تومان است.')
                            else:
                                user_steps[str(chat_id)] = {'step': 'waiting_receipt', 'amount': amount}
                                card_text = (
                                    '💳 <b>اطلاعات پرداخت</b>\n' + DIVIDER + '\n'
                                    '💵 مبلغ: <b>' + f'{amount:,}' + '</b> تومان\n'
                                    '💳 شماره کارت: <code>' + get_card_number() + '</code>\n'
                                    '👤 به نام: <b>' + get_card_owner() + '</b>\n\n'
                                    '📸 پس از واریز، تصویر رسید را ارسال کنید تا برای بررسی به پشتیبانی ارجاع داده شود.'
                                )
                                send_message(chat_id, card_text)
                        except ValueError:
                            send_message(chat_id, '⚠️ لطفاً مبلغ را فقط به‌صورت عدد (تومان) وارد کنید.')

                    elif step.get('step') == 'waiting_receipt' and 'photo' in message:
                        photo_id = message['photo'][-1]['file_id']
                        amount = step.get('amount')
                        req_id = create_topup_request(chat_id, amount)

                        send_message(chat_id, '✅ رسید شما دریافت شد و برای بررسی ارسال گردید. پس از تایید، کیف پول شما شارژ خواهد شد.')
                        user_steps[str(chat_id)] = {}
                        main_menu(chat_id)

                        admin_caption = (
                            '💳 <b>درخواست شارژ کیف پول</b>\n' + DIVIDER + '\n'
                            '👤 کاربر: <code>' + str(chat_id) + '</code>\n'
                            '💵 مبلغ: <b>' + f'{amount:,}' + '</b> تومان\n'
                            '🎫 شناسه درخواست: <b>' + str(req_id) + '</b>'
                        )
                        admin_kb = {
                            'inline_keyboard': [[
                                {'text': '✅ تایید', 'callback_data': 'topup_acc_' + str(req_id)},
                                {'text': '❌ رد کردن', 'callback_data': 'topup_rej_' + str(req_id)}
                            ]]
                        }
                        send_photo(ADMIN_ID, photo_id, caption=admin_caption, reply_markup=admin_kb)

                    elif step.get('step') == 'waiting_receipt' and 'photo' not in message:
                        send_message(chat_id, '📸 لطفاً تصویر رسید واریزی را ارسال کنید.')

                    elif step.get('step') == 'support_message':
                        tid = create_ticket(chat_id, text)
                        send_message(chat_id, '✅ پیام شما با شناسه تیکت #' + str(tid) + ' برای پشتیبانی ثبت شد و به‌زودی پاسخ داده می‌شود.')
                        notify_admin_new_ticket(tid, chat_id, text)
                        user_steps[str(chat_id)] = {}

                    elif step.get('step') == 'admin_reply' and chat_id == ADMIN_ID:
                        tid = step.get('ticket_id')
                        ticket = get_ticket(tid)
                        if ticket:
                            update_ticket_status(tid, 'answered', text)
                            send_message(ticket[1], '📩 پاسخ پشتیبانی برای تیکت #' + str(tid) + ':\n\n' + text)
                            send_message(chat_id, '✅ پاسخ برای کاربر ارسال شد.')
                        else:
                            send_message(chat_id, '⚠️ تیکت یافت نشد.')
                        user_steps[str(chat_id)] = {}

        else:
            time.sleep(1)

    except Exception as loop_error:
        print('⚠️ خطای غیرمنتظره در حلقه اصلی:', loop_error)
        reset_conn()
        time.sleep(2)
