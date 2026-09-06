import subprocess
import sys
import time

# این فایل هر دو ربات (فروش و مدیریت) رو با هم، از یه پروسه، اجرا می‌کنه
# تا هر دو به یک فایل دیتابیس مشترک (shop_data.db) دسترسی داشته باشن.

processes = []


def start_bot(filename):
    print(f"در حال اجرای {filename} ...")
    return subprocess.Popen([sys.executable, filename])


def main():
    processes.append(start_bot("bot-9.py"))
    processes.append(start_bot("admin_sky_bot-1.py"))

    try:
        while True:
            for i, p in enumerate(processes):
                if p.poll() is not None:
                    print(f"یکی از پروسه‌ها متوقف شد (کد خروج {p.returncode})، ری‌استارت می‌شود...")
                    filename = "bot-9.py" if i == 0 else "admin_sky_bot-1.py"
                    processes[i] = start_bot(filename)
            time.sleep(3)
    except KeyboardInterrupt:
        for p in processes:
            p.terminate()


if __name__ == "__main__":
    main()
