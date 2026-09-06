import json
import sqlite3
import time
import os
import requests

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
        ],
        "resize_keyboard": True,
    }
    text = note or "پنل مدیریت — یک گزینه را انتخاب کنید:"
    send_message(chat_id, text, reply_markup=keyboard)


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

                    elif text == "➕ افزایش موجودی":
                        admin_steps[str(chat_id)] = {"step": "ask_id", "sign": 1}
                        send_message(chat_id, "آیدی عددی کاربر را ارسال کنید:\n(برای انصراف بنویس «لغو»)")

                    elif text == "➖ کاهش موجودی":
                        admin_steps[str(chat_id)] = {"step": "ask_id", "sign": -1}
                        send_message(chat_id, "آیدی عددی کاربر را ارسال کنید:\n(برای انصراف بنویس «لغو»)")

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
