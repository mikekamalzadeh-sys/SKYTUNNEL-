import json
import sqlite3
import time
import os
import requests
from datetime import datetime

TOKEN = os.getenv("ADMIN_BOT_TOKEN", "69799798:gwpKKgKLMwVp3kxAdjQwNGwx7ZLq2puNE9A")
BASE_URL = f"https://api.splus.ir/bot{TOKEN}"
ADMIN_ID = int(os.getenv("ADMIN_ID", "48198481"))

admin_steps = {}
CANCEL_WORDS = {"لغو", "انصراف", "/cancel"}
USERS_PAGE_SIZE = 10


# ---------------------------------------------------------------------------
# دیتابیس (همون shop_data.db که ربات فروش می‌سازه)
# ---------------------------------------------------------------------------

def get_conn():
    return sqlite3.connect("shop_data.db")


def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, wallet INTEGER DEFAULT 0)")
    c.execute('''CREATE TABLE IF NOT EXISTS discount_codes (
        code TEXT PRIMARY KEY,
        percent INTEGER,
        max_uses INTEGER,
        used_count INTEGER DEFAULT 0,
        active INTEGER DEFAULT 1,
        created_at TEXT
    )''')
    for statement in (
        "ALTER TABLE users ADD COLUMN username TEXT",
        "ALTER TABLE users ADD COLUMN first_name TEXT",
    ):
        try:
            c.execute(statement)
        except Exception:
            pass
    conn.commit()
    conn.close()


def get_wallet(user_id):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT wallet FROM users WHERE user_id=?", (user_id,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else None


def update_wallet(user_id, amount):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO users (user_id, wallet) VALUES (?, ?)", (user_id, 0))
    c.execute("UPDATE users SET wallet = wallet + ? WHERE user_id=?", (amount, user_id))
    conn.commit()
    conn.close()


def create_discount_code(code, percent, max_uses):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "INSERT INTO discount_codes (code, percent, max_uses, used_count, active, created_at) VALUES (?, ?, ?, 0, 1, ?)",
        (code.strip().upper(), percent, max_uses, datetime.now().isoformat()),
    )
    conn.commit()
    conn.close()


def code_exists(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM discount_codes WHERE code=?", (code.strip().upper(),))
    res = c.fetchone()
    conn.close()
    return res is not None


def get_all_discount_codes():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT code, percent, max_uses, used_count, active FROM discount_codes ORDER BY created_at DESC")
    res = c.fetchall()
    conn.close()
    return res


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


def delete_discount_code(code):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM discount_codes WHERE code=?", (code,))
    conn.commit()
    conn.close()


FEATURES = {
    "buy": "🛍 خرید کانفیگ",
    "trial": "🧪 تست رایگان",
    "topup": "💠 شارژ کیف پول",
    "delete_config": "🗑 حذف کانفیگ",
    "support": "🎧 پشتیبانی",
}


def get_setting(key, default="1"):
    conn = get_conn()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute("SELECT value FROM settings WHERE key=?", (key,))
    res = c.fetchone()
    conn.close()
    return res[0] if res else default


def set_setting(key, value):
    conn = get_conn()
    c = conn.cursor()
    c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
    c.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()
    conn.close()


def get_users_count():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]
    conn.close()
    return total


def get_users_page(offset=0, limit=USERS_PAGE_SIZE):
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT user_id, username, first_name, wallet FROM users ORDER BY user_id LIMIT ? OFFSET ?",
        (limit, offset),
    )
    res = c.fetchall()
    conn.close()
    return res


# ---------------------------------------------------------------------------
# API تلگرام
# ---------------------------------------------------------------------------

def send_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = json.dumps(reply_markup)
    try:
        res = requests.post(BASE_URL + "/sendMessage", json=payload, timeout=15)
        return res.json()
    except Exception:
        return {"ok": False}


def get_updates(offset=None):
    params = {"timeout": 3}
    if offset:
        params["offset"] = offset
    try:
        res = requests.get(BASE_URL + "/getUpdates", params=params, timeout=10)
        return res.json()
    except Exception:
        return {"ok": False, "result": []}


def answer_callback(callback_query_id):
    try:
        requests.post(BASE_URL + "/answerCallbackQuery", json={"callback_query_id": callback_query_id}, timeout=5)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# رابط کاربری
# ---------------------------------------------------------------------------

def main_menu(chat_id, note=None):
    keyboard = {
        "keyboard": [
            [{"text": "📋 لیست کاربران"}],
            [{"text": "➕ افزایش موجودی"}, {"text": "➖ کاهش موجودی"}],
            [{"text": "🔌 مدیریت قابلیت‌ها"}],
            [{"text": "🎟 کدهای تخفیف"}],
        ],
        "resize_keyboard": True,
    }
    text = note or "پنل مدیریت — یک گزینه را انتخاب کنید:"
    send_message(chat_id, text, reply_markup=keyboard)


def features_keyboard():
    buttons = []
    for key, label in FEATURES.items():
        enabled = get_setting(key, "1") == "1"
        status = "✅ روشن" if enabled else "⛔ خاموش"
        buttons.append([{"text": f"{label} — {status}", "callback_data": f"toggle_{key}"}])
    return {"inline_keyboard": buttons}


def send_features_menu(chat_id):
    send_message(chat_id, "مدیریت قابلیت‌های ربات فروش — با لمس هرکدوم وضعیتش عوض می‌شود:", reply_markup=features_keyboard())


def discount_codes_menu_keyboard():
    return {
        "inline_keyboard": [
            [{"text": "➕ ساخت کد تخفیف جدید", "callback_data": "new_discount"}],
            [{"text": "📋 لیست کدهای تخفیف", "callback_data": "list_discounts"}],
        ]
    }


def send_discount_codes_menu(chat_id):
    send_message(chat_id, "مدیریت کدهای تخفیف:", reply_markup=discount_codes_menu_keyboard())


def discount_codes_list_keyboard():
    codes = get_all_discount_codes()
    buttons = []
    for code, percent, max_uses, used_count, active in codes:
        status = "✅ فعال" if active == 1 else "⛔ غیرفعال"
        label = f"{code} | {percent}٪ | {used_count}/{max_uses} | {status}"
        buttons.append([
            {"text": label, "callback_data": f"toggle_discount_{code}"},
            {"text": "🗑", "callback_data": f"delete_discount_{code}"},
        ])
    buttons.append([{"text": "🔙 بازگشت", "callback_data": "discount_menu"}])
    return {"inline_keyboard": buttons}


def send_discount_codes_list(chat_id):
    codes = get_all_discount_codes()
    if not codes:
        send_message(chat_id, "هنوز هیچ کد تخفیفی ساخته نشده.", reply_markup=discount_codes_menu_keyboard())
        return
    send_message(
        chat_id,
        "لیست کدهای تخفیف (روی نام کد بزنید تا فعال/غیرفعال شود، روی 🗑 بزنید تا حذف شود):",
        reply_markup=discount_codes_list_keyboard(),
    )


def format_users_page(rows, offset, total):
    lines = [f"لیست کاربران ({offset + 1}-{offset + len(rows)} از {total})", ""]
    for user_id, username, first_name, wallet in rows:
        handle = f"@{username}" if username else "بدون یوزرنیم"
        name = first_name or "-"
        lines.append(f"🆔 {user_id} | {handle} | {name}\n👛 موجودی: {wallet:,} تومان\n")
    return "\n".join(lines)


def users_page_keyboard(offset, total):
    nav_row = []
    if offset > 0:
        nav_row.append({"text": "⬅️ قبلی", "callback_data": f"page_{max(0, offset - USERS_PAGE_SIZE)}"})
    if offset + USERS_PAGE_SIZE < total:
        nav_row.append({"text": "➡️ بعدی", "callback_data": f"page_{offset + USERS_PAGE_SIZE}"})
    return {"inline_keyboard": [nav_row]} if nav_row else None


def send_users_page(chat_id, offset=0):
    total = get_users_count()
    if total == 0:
        send_message(chat_id, "هنوز هیچ کاربری ثبت نشده.")
        return
    rows = get_users_page(offset=offset)
    if not rows:
        send_message(chat_id, "صفحه‌ی دیگری وجود ندارد.")
        return
    send_message(chat_id, format_users_page(rows, offset, total), reply_markup=users_page_keyboard(offset, total))


# ---------------------------------------------------------------------------
# حلقه‌ی اصلی
# ---------------------------------------------------------------------------

init_db()
print("ربات مدیریت ساده روشن شد...")
last_update_id = 0

while True:
    try:
        updates = get_updates(last_update_id + 1)

        if updates.get("ok") and updates.get("result"):
            for update in updates["result"]:
                last_update_id = update["update_id"]

                sender_id = None
                if "message" in update:
                    sender_id = update["message"]["chat"]["id"]
                elif "callback_query" in update:
                    sender_id = update["callback_query"]["message"]["chat"]["id"]
                if sender_id != ADMIN_ID:
                    continue

                # ------------------------- دکمه‌های شیشه‌ای -------------------------
                if "callback_query" in update:
                    query = update["callback_query"]
                    chat_id = query["message"]["chat"]["id"]
                    data = query.get("data", "")
                    answer_callback(query["id"])

                    if data.startswith("page_"):
                        offset = int(data.split("_")[1])
                        send_users_page(chat_id, offset=offset)

                    elif data.startswith("toggle_discount_"):
                        code = data[len("toggle_discount_"):]
                        toggle_discount_code(code)
                        send_discount_codes_list(chat_id)

                    elif data.startswith("delete_discount_"):
                        code = data[len("delete_discount_"):]
                        delete_discount_code(code)
                        send_discount_codes_list(chat_id)

                    elif data.startswith("toggle_"):
                        key = data[len("toggle_"):]
                        if key in FEATURES:
                            current = get_setting(key, "1")
                            set_setting(key, "0" if current == "1" else "1")
                        send_features_menu(chat_id)

                    elif data == "discount_menu":
                        send_discount_codes_menu(chat_id)

                    elif data == "list_discounts":
                        send_discount_codes_list(chat_id)

                    elif data == "new_discount":
                        admin_steps[str(chat_id)] = {"step": "ask_discount_code"}
                        send_message(chat_id, "نام کد تخفیف را وارد کنید (مثلاً OFF20):\n(برای انصراف بنویس «لغو»)")

                # ------------------------- پیام‌های متنی -------------------------
                elif "message" in update:
                    message = update["message"]
                    chat_id = message["chat"]["id"]
                    text = message.get("text", "")
                    step = admin_steps.get(str(chat_id), {})

                    if text in CANCEL_WORDS and step:
                        admin_steps[str(chat_id)] = {}
                        main_menu(chat_id, "لغو شد.")
                        continue

                    if text == "/start":
                        admin_steps[str(chat_id)] = {}
                        main_menu(chat_id)

                    elif text == "📋 لیست کاربران":
                        send_users_page(chat_id, offset=0)

                    elif text == "🔌 مدیریت قابلیت‌ها":
                        send_features_menu(chat_id)

                    elif text == "🎟 کدهای تخفیف":
                        send_discount_codes_menu(chat_id)

                    elif text == "➕ افزایش موجودی":
                        admin_steps[str(chat_id)] = {"step": "ask_id", "sign": 1}
                        send_message(chat_id, "آیدی عددی کاربر را ارسال کنید:\n(برای انصراف بنویس «لغو»)")

                    elif text == "➖ کاهش موجودی":
                        admin_steps[str(chat_id)] = {"step": "ask_id", "sign": -1}
                        send_message(chat_id, "آیدی عددی کاربر را ارسال کنید:\n(برای انصراف بنویس «لغو»)")

                    elif step.get("step") == "ask_discount_code":
                        code = text.strip().upper()
                        if not code or " " in code:
                            send_message(chat_id, "نام کد نامعتبر است. بدون فاصله وارد کنید:")
                        elif code_exists(code):
                            send_message(chat_id, "این کد از قبل وجود دارد. نام دیگری وارد کنید:")
                        else:
                            admin_steps[str(chat_id)] = {"step": "ask_discount_percent", "code": code}
                            send_message(chat_id, "چند درصد تخفیف اعمال شود؟ (عددی بین ۱ تا ۹۹)")

                    elif step.get("step") == "ask_discount_percent":
                        try:
                            percent = int(text)
                            if not (1 <= percent <= 99):
                                send_message(chat_id, "درصد باید بین ۱ تا ۹۹ باشد.")
                            else:
                                admin_steps[str(chat_id)] = {
                                    "step": "ask_discount_max_uses",
                                    "code": step["code"],
                                    "percent": percent,
                                }
                                send_message(chat_id, "این کد حداکثر چند بار قابل استفاده باشد؟ (عدد وارد کنید)")
                        except ValueError:
                            send_message(chat_id, "فقط عدد وارد کنید.")

                    elif step.get("step") == "ask_discount_max_uses":
                        try:
                            max_uses = int(text)
                            if max_uses <= 0:
                                send_message(chat_id, "عددی بزرگ‌تر از صفر وارد کنید.")
                            else:
                                code = step["code"]
                                percent = step["percent"]
                                create_discount_code(code, percent, max_uses)
                                admin_steps[str(chat_id)] = {}
                                send_message(
                                    chat_id,
                                    f"✅ کد تخفیف ساخته شد.\n🎟 کد: {code}\n💯 درصد: {percent}٪\n🔁 حداکثر استفاده: {max_uses} بار",
                                )
                                main_menu(chat_id)
                        except ValueError:
                            send_message(chat_id, "فقط عدد وارد کنید.")

                    elif step.get("step") == "ask_id":
                        try:
                            target_id = int(text)
                            wallet = get_wallet(target_id)
                            if wallet is None:
                                send_message(chat_id, "کاربری با این آیدی پیدا نشد.")
                                admin_steps[str(chat_id)] = {}
                            else:
                                admin_steps[str(chat_id)] = {
                                    "step": "ask_amount",
                                    "sign": step["sign"],
                                    "target_id": target_id,
                                }
                                send_message(chat_id, f"موجودی فعلی: {wallet:,} تومان\nمبلغ را به تومان وارد کنید:")
                        except ValueError:
                            send_message(chat_id, "فقط آیدی عددی وارد کنید.")

                    elif step.get("step") == "ask_amount":
                        try:
                            amount = int(text)
                            if amount <= 0:
                                send_message(chat_id, "عددی بزرگ‌تر از صفر وارد کنید.")
                            else:
                                target_id = step["target_id"]
                                signed = amount * step["sign"]
                                update_wallet(target_id, signed)
                                new_wallet = get_wallet(target_id)
                                action = "افزایش" if signed > 0 else "کاهش"
                                send_message(chat_id, f"✅ موجودی کاربر {target_id} به مقدار {amount:,} تومان {action} یافت.\nموجودی جدید: {new_wallet:,} تومان")
                                admin_steps[str(chat_id)] = {}
                                main_menu(chat_id)
                        except ValueError:
                            send_message(chat_id, "فقط عدد وارد کنید (به تومان).")

        else:
            time.sleep(1)

    except Exception as loop_error:
        print("خطای غیرمنتظره در حلقه اصلی:", loop_error)
        time.sleep(2)
    
