"""
Crypto Performance Tracker
---------------------------
این اسکریپت هر بار که اجرا می‌شود:
1. سیگنال‌های تازه (کوین‌های معرفی‌شده) را مستقیماً از ریپوی اسکرینر می‌خواند.
2. کوین‌های جدید را به لیست پیگیری اضافه می‌کند (کوین‌های تکراری فقط شمارشگرشان زیاد می‌شود).
3. قیمت فعلی همه کوین‌های پیگیری‌شده (چه تازه چه قدیمی) را می‌گیرد و به تاریخچه اضافه می‌کند.
4. یک پیام کوتاه وضعیت را به تلگرام می‌فرستد.

نمودارها اینجا ساخته نمی‌شوند؛ صفحه index.html مستقیماً از فایل‌های JSON این
پوشه (data/) نمودار تعاملی می‌سازد.
"""

import os
import json
import time
from datetime import datetime, timezone

import requests

SCREENER_REPO = "hamed7razi-oss/crypto-growth-screener"
SCREENER_SIGNALS_API = f"https://api.github.com/repos/{SCREENER_REPO}/contents/signals"
COINGECKO_BASE = "https://api.coingecko.com/api/v3"

DATA_DIR = "data"
TRACKED_PATH = f"{DATA_DIR}/tracked_coins.json"
HISTORY_PATH = f"{DATA_DIR}/price_history.json"
PROCESSED_PATH = f"{DATA_DIR}/processed_signals.json"

DASHBOARD_URL = "https://hamed7razi-oss.github.io/crypto-performance-tracker/"

TELEGRAM_BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_CHAT_ID = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()


# ---------------------------------------------------------------------------
# ابزارهای خواندن/نوشتن فایل
# ---------------------------------------------------------------------------
def load_json(path, default):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default


def save_json(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# ۱) خواندن سیگنال‌های تازه از ریپوی اسکرینر
# ---------------------------------------------------------------------------
def fetch_new_signals(processed):
    headers = {}
    gh_token = os.environ.get("GITHUB_TOKEN")
    if gh_token:
        headers["Authorization"] = f"Bearer {gh_token}"
        headers["Accept"] = "application/vnd.github+json"

    resp = requests.get(SCREENER_SIGNALS_API, headers=headers, timeout=15)
    if resp.status_code == 404:
        print("هنوز هیچ سیگنالی در ریپوی اسکرینر ثبت نشده.")
        return []
    if resp.status_code != 200:
        print(f"خطا در خواندن سیگنال‌ها از گیت‌هاب: {resp.status_code} {resp.text[:300]}")
        return []

    files = resp.json()
    new_signals = []
    for f in files:
        name = f.get("name")
        if not name or not name.endswith(".json") or name in processed:
            continue
        raw = requests.get(f["download_url"], headers=headers, timeout=15)
        if raw.status_code == 200:
            try:
                new_signals.append((name, raw.json()))
            except ValueError:
                print(f"فایل سیگنال نامعتبر: {name}")
        time.sleep(0.3)

    new_signals.sort(key=lambda x: x[0])  # ترتیب زمانی (اسم فایل = timestamp)
    return new_signals


# ---------------------------------------------------------------------------
# ۲) پیدا کردن coin_id در CoinGecko بر اساس نماد
# ---------------------------------------------------------------------------
def resolve_coin_id(symbol):
    clean_symbol = symbol.split(".")[0]
    resp = requests.get(f"{COINGECKO_BASE}/search", params={"query": clean_symbol}, timeout=15)
    if resp.status_code != 200:
        return None
    coins = resp.json().get("coins", [])
    for c in coins:
        if c.get("symbol", "").upper() == clean_symbol.upper():
            return c["id"]
    return coins[0]["id"] if coins else None


# ---------------------------------------------------------------------------
# ۳) اضافه کردن سیگنال‌های تازه به لیست پیگیری
# ---------------------------------------------------------------------------
def ingest_signals(tracked, new_signals):
    by_symbol = {c["symbol"]: c for c in tracked}
    newly_added = []

    for _fname, items in new_signals:
        for item in items:
            symbol = item.get("symbol")
            if not symbol:
                continue

            if symbol in by_symbol:
                existing = by_symbol[symbol]
                existing["repeat_count"] = existing.get("repeat_count", 1) + 1
                existing["latest_score"] = item.get("score")
                if item.get("reasons"):
                    existing["latest_reasons"] = item["reasons"]
                if item.get("rsi") is not None:
                    existing["latest_rsi"] = item["rsi"]
                # اگه شناسه دقیق کوین تو سیگنال جدید بود ولی قبلاً نداشتیم (سیگنال‌های قدیمی‌تر)، الان درستش کن
                if item.get("coin_id") and not existing.get("coin_id_confirmed"):
                    existing["coin_id"] = item["coin_id"]
                    existing["coin_id_confirmed"] = True
                continue

            # ترجیح اول: شناسه دقیقی که خودِ اسکرینر گزارش کرده (بدون ابهام)
            # ترجیح دوم (فقط برای سیگنال‌های خیلی قدیمی که این فیلد رو نداشتن): حدس از رو اسم
            coin_id = item.get("coin_id")
            confirmed = bool(coin_id)
            if not coin_id:
                coin_id = resolve_coin_id(symbol)
                time.sleep(1.2)

            entry = {
                "symbol": symbol,
                "coin_id": coin_id,
                "coin_id_confirmed": confirmed,
                "name": item.get("name"),
                "score": item.get("score"),
                "latest_score": item.get("score"),
                "entry_price": item.get("price") or None,
                "first_seen_at": datetime.now(timezone.utc).isoformat(),
                "repeat_count": 1,
                "reasons": item.get("reasons", []),
                "latest_reasons": item.get("reasons", []),
                "entry_rsi": item.get("rsi"),
                "latest_rsi": item.get("rsi"),
                "entry_alpha_vs_btc": item.get("alpha_vs_btc"),
            }
            by_symbol[symbol] = entry
            tracked.append(entry)
            newly_added.append(symbol)

    return newly_added


# ---------------------------------------------------------------------------
# ۴) گرفتن قیمت فعلی همه کوین‌های پیگیری‌شده در یک درخواست
# ---------------------------------------------------------------------------
def fetch_prices(tracked):
    """
    قیمت‌ها رو دسته‌دسته می‌گیره (نه همه با هم)، چون وقتی تعداد کوین‌های
    پیگیری‌شده زیاد بشه (مثلاً بالای ۱۰۰ تا)، یک درخواست با همه شناسه‌ها
    ممکنه به‌خاطر طولانی بودن آدرس رد بشه.
    """
    ids = sorted(set([c["coin_id"] for c in tracked if c.get("coin_id")] + ["bitcoin"]))
    if not ids:
        return {}

    all_prices = {}
    batch_size = 100
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        resp = requests.get(
            f"{COINGECKO_BASE}/simple/price",
            params={"ids": ",".join(batch), "vs_currencies": "usd"},
            timeout=25,
        )
        if resp.status_code != 200:
            print(f"خطا در گرفتن قیمت دسته {i//batch_size + 1}: {resp.status_code} {resp.text[:200]}")
            time.sleep(2)
            continue
        all_prices.update(resp.json())
        time.sleep(1.5)

    return all_prices


# ---------------------------------------------------------------------------
# ۵) ارسال پیام کوتاه وضعیت به تلگرام
# ---------------------------------------------------------------------------
def send_telegram_text(text):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ توکن یا Chat ID تلگرام تنظیم نشده.")
        print(text)
        return
    resp = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True},
        timeout=10,
    )
    if resp.status_code != 200:
        print(f"خطا در ارسال تلگرام: {resp.text}")


def build_alert(tracked, history, newly_added):
    if len(history) < 2:
        return (
            f"✅ ردیاب عملکرد راه‌اندازی شد.\n"
            f"در حال پیگیری {len(tracked)} کوین.\n"
            f"📊 داشبورد: {DASHBOARD_URL}"
        )

    latest = history[-1]["prices"]
    total, baseline = 0.0, 0.0
    winners = 0
    for c in tracked:
        price = latest.get(c["symbol"])
        entry = c.get("entry_price")
        baseline += 100
        if price and entry:
            value = 100 * price / entry
            total += value
            if value > 100:
                winners += 1
        else:
            total += 100

    pct = (total / baseline - 1) * 100 if baseline else 0
    win_rate = (winners / len(tracked) * 100) if tracked else 0

    lines = [
        "🔔 <b>آپدیت ردیاب عملکرد</b>",
        f"پرتفوی: ${total:.0f} از ${baseline:.0f} ({pct:+.2f}%)",
        f"نرخ موفقیت: {win_rate:.0f}% از {len(tracked)} کوین",
    ]
    if newly_added:
        lines.append(f"🆕 کوین‌های تازه اضافه‌شده: {', '.join(newly_added)}")
    lines.append(f"📊 داشبورد کامل: {DASHBOARD_URL}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# اجرای اصلی
# ---------------------------------------------------------------------------
def main():
    tracked = load_json(TRACKED_PATH, [])
    history = load_json(HISTORY_PATH, [])
    processed = load_json(PROCESSED_PATH, [])

    new_signals = fetch_new_signals(processed)
    newly_added = ingest_signals(tracked, new_signals)
    for fname, _ in new_signals:
        processed.append(fname)

    if not tracked:
        print("هنوز هیچ کوینی برای پیگیری وجود ندارد.")
        save_json(PROCESSED_PATH, processed)
        return

    prices = fetch_prices(tracked)

    # اگر قیمت ورود کوینی نامعلوم بود (مثلاً قیمت خیلی کوچک گزارش‌شده به‌صورت صفر)،
    # از اولین قیمت واقعی که الان گرفتیم به‌عنوان مبنا استفاده کن
    for c in tracked:
        if not c.get("entry_price"):
            live = prices.get(c.get("coin_id"), {}).get("usd")
            if live:
                c["entry_price"] = live

    snapshot = {"timestamp": datetime.now(timezone.utc).isoformat(), "prices": {}}
    for c in tracked:
        price = prices.get(c.get("coin_id"), {}).get("usd")
        snapshot["prices"][c["symbol"]] = price
    snapshot["prices"]["bitcoin"] = prices.get("bitcoin", {}).get("usd")
    history.append(snapshot)

    save_json(TRACKED_PATH, tracked)
    save_json(HISTORY_PATH, history)
    save_json(PROCESSED_PATH, processed)

    alert = build_alert(tracked, history, newly_added)
    print(alert)
    send_telegram_text(alert)


if __name__ == "__main__":
    main()
