import requests
import time
import json
import sqlite3
import os
from datetime import datetime, timedelta

TOKEN = os.getenv("BOT_TOKEN", "69786607:U_ltmyh-8XS6RuBUsLNiIVi9l0Mq0aekXvE")
BASE_URL = "https://api.splus.ir/bot" + TOKEN
CONFIG_API = os.getenv("CONFIG_API", "https://su.randomatic.ir/api/v1/configs")
CONFIG_KEY = os.getenv("CONFIG_KEY", "sk_live_azIaKWpOvQDoD2-7vX8-yyf3WNPg6U1p")
ADMIN_ID = int(os.getenv("ADMIN_ID", "48198481"))
PRICE_PER_GB = int(os.getenv("PRICE_PER_GB", "3000"))
CARD_NUMBER = os.getenv("CARD_NUMBER", "6219861957006504")
CARD_OWNER = os.getenv("CARD_OWNER", "کمالزاده")
MIN_TOPUP = int(os.getenv("MIN_TOPUP", "10000"))

DIVIDER = "┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄"

user_steps = {}


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

def get_conn():
    return sqlite3.connect('shop_data.db')


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
    ):
        try:
            c.execute(statement)
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_wallet_db(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT wallet FROM users WHERE user_id=?', (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else 0


def add_user(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, wallet, joined_at) VALUES (?, ?, ?)',
               (user_id, 0, datetime.now().isoformat()))
    conn.commit()
    conn.close()


def upsert_user_info(user_id, username, first_name):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, wallet, joined_at) VALUES (?, ?, ?)',
               (user_id, 0, datetime.now().isoformat()))
    c.execute('UPDATE users SET username=?, first_name=? WHERE user_id=?',
               (username, first_name, user_id))
    conn.commit()
    conn.close()


def update_wallet_db(user_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO users (user_id, wallet) VALUES (?, ?)', (user_id, 0))
    c.execute('UPDATE users SET wallet = wallet + ? WHERE user_id=?', (amount, user_id))
    conn.commit()
    conn.close()


def save_user_config(user_id, sub_url, config_id, gb, label, days, price):
    conn = get_conn()
    c = conn.cursor()
    expires_at = (datetime.now() + timedelta(days=days)).isoformat()
    c.execute('''INSERT INTO user_configs
        (user_id, sub_url, config_id, gb, label, days, price, created_at, expires_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)''',
        (user_id, sub_url, config_id, gb, label, days, price, datetime.now().isoformat(), expires_at))
    conn.commit()
    conn.close()


def get_user_configs(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('''SELECT id, sub_url, config_id, gb, label, days, price, created_at, expires_at
        FROM user_configs WHERE user_id=? ORDER BY created_at DESC''', (user_id,))
    res = c.fetchall()
    conn.close()
    return res


def delete_config_from_panel(config_id):
    if not config_id:
        return True
    try:
        res = requests.delete(
            CONFIG_API + '/' + str(config_id),
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            timeout=15
        )
        return res.status_code in (200, 201, 204)
    except Exception:
        return False


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


def get_total_purchases(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT SUM(gb), SUM(price) FROM user_configs WHERE user_id=?', (user_id,))
    res = c.fetchone()
    conn.close()
    return (res[0] if res and res[0] else 0, res[1] if res and res[1] else 0)


def check_free_trial(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM free_trials WHERE user_id=?', (user_id,))
    count = c.fetchone()[0]
    conn.close()
    return count


def set_free_trial(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT OR IGNORE INTO free_trials (user_id) VALUES (?)', (user_id,))
    conn.commit()
    conn.close()


def create_topup_request(user_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT INTO topup_requests (user_id, amount, created_at) VALUES (?, ?, ?)',
               (user_id, amount, datetime.now().isoformat()))
    conn.commit()
    req_id = c.lastrowid
    conn.close()
    return req_id


def get_topup_request(req_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT id, user_id, amount, status FROM topup_requests WHERE id=?', (req_id,))
    res = c.fetchone()
    conn.close()
    return res


def update_topup_status(req_id, status):
    conn = get_conn()
    c = conn.cursor()
    c.execute('UPDATE topup_requests SET status=? WHERE id=?', (status, req_id))
    conn.commit()
    conn.close()


def create_ticket(user_id, message):
    conn = get_conn()
    c = conn.cursor()
    c.execute('INSERT INTO tickets (user_id, message, created_at) VALUES (?, ?, ?)',
               (user_id, message, datetime.now().isoformat()))
    conn.commit()
    tid = c.lastrowid
    conn.close()
    return tid


def get_ticket(ticket_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT id, user_id, message, status, admin_reply, created_at FROM tickets WHERE id=?', (ticket_id,))
    res = c.fetchone()
    conn.close()
    return res


def update_ticket_status(ticket_id, status, admin_reply=None):
    conn = get_conn()
    c = conn.cursor()
    if admin_reply:
        c.execute('UPDATE tickets SET status=?, admin_reply=? WHERE id=?', (status, admin_reply, ticket_id))
    else:
        c.execute('UPDATE tickets SET status=? WHERE id=?', (status, ticket_id))
    conn.commit()
    conn.close()


def get_discount_code(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute('SELECT code, percent, max_uses, used_count, active FROM discount_codes WHERE code=?',
              (code.strip().upper(),))
    res = c.fetchone()
    conn.close()
    return res


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

def get_updates(offset=None):
    url = BASE_URL + '/getUpdates'
    params = {'timeout': 3}
    if offset:
        params['offset'] = offset
    try:
        res = requests.get(url, params=params, timeout=10)
        return res.json()
    except Exception:
        return {'ok': False, 'result': []}


def send_message(chat_id, text, parse_mode='HTML', reply_markup=None):
    url = BASE_URL + '/sendMessage'
    payload = {'chat_id': chat_id, 'text': text}
    if parse_mode:
        payload['parse_mode'] = parse_mode
    if reply_markup:
        payload['reply_markup'] = json.dumps(reply_markup)
    try:
        res = requests.post(url, json=payload, timeout=15)
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
        res = requests.post(url, json=payload, timeout=20)
        return res.json()
    except Exception:
        return {'ok': False}


def answer_callback(callback_query_id):
    try:
        requests.post(BASE_URL + '/answerCallbackQuery', json={'callback_query_id': callback_query_id}, timeout=5)
    except Exception:
        pass


def make_config(gb, label, days):
    try:
        res = requests.post(
            CONFIG_API,
            headers={'Authorization': 'Bearer ' + CONFIG_KEY},
            json={'gb': gb, 'label': label, 'expiryDays': days, 'proto': 'both'},
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
        return {'success': False, 'error': str(res.status_code)}
    except Exception as e:
        return {'success': False, 'error': str(e)}


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def main_menu(chat_id, note=None):
    wallet = get_wallet_db(chat_id)
    keyboard = {
        'keyboard': [
            [{'text': '🛍 خرید کانفیگ'}, {'text': '🧪 تست رایگان'}],
            [{'text': '💠 شارژ کیف پول'}],
            [{'text': '👤 حساب من'}],
            [{'text': '🗂 کانفیگ‌های من'}, {'text': '🗑 حذف کانفیگ'}],
            [{'text': '🎧 پشتیبانی'}]
        ],
        'resize_keyboard': True
    }
    text = (
        '✨ <b>به ربات فروش خوش آمدید</b>\n' + DIVIDER + '\n'
        '💎 نرخ هر گیگابایت: <b>' + f'{PRICE_PER_GB:,}' + '</b> تومان\n'
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

init_db()
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

                    if data == 'confirm_purchase':
                        s = user_steps.get(str(chat_id), {})
                        if s.get('step') == 'confirm_buy':
                            gb, label, days, price = s['gb'], s['label'], s['days'], s['price']
                            if get_wallet_db(chat_id) < price:
                                send_message(chat_id, '⚠️ موجودی کیف پول کافی نیست.')
                            else:
                                send_message(chat_id, '⏳ در حال ساخت کانفیگ، لطفاً شکیبا باشید...')
                                res = make_config(gb, label, days)
                                if res['success'] and res['sub_url']:
                                    update_wallet_db(chat_id, -price)
                                    save_user_config(chat_id, res['sub_url'], res['config_id'], gb, label, days, price)
                                    if s.get('discount_code'):
                                        increment_discount_usage(s['discount_code'])
                                    success_text = (
                                        '🎉 <b>کانفیگ شما با موفقیت ساخته شد</b>\n' + DIVIDER + '\n'
                                        '📶 حجم: <b>' + str(gb) + '</b> گیگابایت\n'
                                        '🏷 نام: <b>' + str(label) + '</b>\n'
                                        '⏳ مدت اعتبار: <b>' + str(days) + '</b> روز\n'
                                        '💵 قیمت: <b>' + f'{price:,}' + '</b> تومان\n' + DIVIDER + '\n'
                                        '🔗 لینک سابسکریپشن:\n<code>' + str(res['sub_url']) + '</code>'
                                    )
                                    send_message(chat_id, success_text)
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
                            user_steps[str(chat_id)] = {
                                'step': 'confirm_buy',
                                'gb': gb,
                                'days': days,
                                'price': price,
                                'label': label
                            }
                            confirm_text = (
                                '🧾 <b>تایید نهایی خرید</b>\n' + DIVIDER + '\n'
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
                    
