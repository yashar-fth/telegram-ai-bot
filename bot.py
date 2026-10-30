import os
import re
import json
import time
import logging
import argparse
import urllib.request
import urllib.error
from datetime import datetime

# Configure logging
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9289068692:AAFRmiyrGctviIKkuTutNbWdnNF9c9NkU74")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "8924907701")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyADkE4rzliwDYWlZVSKCFlZ-8VKQLqc124")
DEFAULT_CHANNELS = ["rodast_omiddana"]

# Models to attempt (fallback sequence)
GEMINI_MODELS = ["gemini-3.8-flash", "gemini-flash-latest", "gemini-2.5-flash"]
SEEN_POSTS_FILE = "seen_posts.json"


def load_seen_posts():
    """Load previously summarized post IDs."""
    if os.path.exists(SEEN_POSTS_FILE):
        try:
            with open(SEEN_POSTS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logging.error(f"Error loading seen posts: {e}")
    return {}


def save_seen_posts(seen_dict):
    """Save seen post IDs to file."""
    try:
        with open(SEEN_POSTS_FILE, "w", encoding="utf-8") as f:
            json.dump(seen_dict, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.error(f"Error saving seen posts: {e}")


def send_telegram_message(chat_id, text, parse_mode="HTML"):
    """Send a message via Telegram Bot API."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": False
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_msg = e.read().decode("utf-8")
        logging.error(f"Telegram API HTTP error {e.code}: {err_msg}")
        # If HTML parse error, try sending plain text
        if "can't parse entities" in err_msg:
            payload.pop("parse_mode", None)
            data_plain = json.dumps(payload).encode("utf-8")
            req_plain = urllib.request.Request(url, data=data_plain, headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req_plain, timeout=15) as fallback_resp:
                return json.loads(fallback_resp.read().decode("utf-8"))
        return None
    except Exception as e:
        logging.error(f"Telegram API error: {e}")
        return None


def call_gemini(prompt, system_instruction=None):
    """Call Google Gemini API."""
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={GEMINI_API_KEY}"
        
        payload = {
            "contents": [{
                "parts": [{"text": prompt}]
            }],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 1000
            }
        }
        
        if system_instruction:
            payload["systemInstruction"] = {
                "parts": [{"text": system_instruction}]
            }

        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                res = json.loads(resp.read().decode("utf-8"))
                text = res["candidates"][0]["content"]["parts"][0]["text"]
                return text.strip()
        except urllib.error.HTTPError as e:
            err = e.read().decode("utf-8")
            logging.warning(f"Gemini {model} HTTP error {e.code}: {err[:150]}")
            if e.code == 404:
                continue
            break
        except Exception as e:
            logging.warning(f"Gemini {model} exception: {e}")
            continue
            
    return "متأسفانه در حال حاضر امکان برقراری ارتباط با هوش مصنوعی وجود ندارد. لطفاً بعداً تلاش کنید."


def summarize_text(text, channel_name=""):
    """Summarize post using Gemini."""
    system_instruction = (
        "تو یک دستیار هوشمند و تحلیل‌گر خبر هستی. "
        "وظیفه تو این است که متن پست‌های تلگرامی را به زبان فارسی روان، جذاب و بسیار خوانا خلاصه کنی. "
        "نکات مهم را به شکل تیتروار (Bullet points) همراه با ایموجی‌های مناسب بنویس. "
        "از حاشیه‌پردازی دوری کن و فقط پیام اصلی و نکات کلیدی پست را بیان کن."
    )
    prompt = f"لطفاً متن زیر از کانال @{channel_name} را به صورت جامع، دقیق و تیتروار خلاصه کن:\n\n{text}"
    return call_gemini(prompt, system_instruction)


def fetch_channel_posts(channel_username):
    """Fetch recent posts from public Telegram channel web preview."""
    clean_channel = channel_username.replace("@", "").replace("https://t.me/", "").replace("http://t.me/", "").strip("/")
    url = f"https://t.me/s/{clean_channel}"
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", errors="ignore")
    except Exception as e:
        logging.error(f"Failed to fetch channel @{clean_channel}: {e}")
        return []

    # Extract posts with data-post attribute
    # Pattern captures: data-post="channel/123" and the post text
    raw_posts = re.findall(
        r'data-post="([^"]+)".*?<div class="tgme_widget_message_text[^>]*>(.*?)</div>',
        html,
        re.DOTALL
    )

    results = []
    for post_id, raw_html in raw_posts:
        # Clean up HTML tags
        text = re.sub(r'<br\s*/?>', '\n', raw_html)
        text = re.sub(r'<[^>]+>', '', text).strip()
        if len(text) > 30: # Ignore very short media captions / stickers
            results.append({
                "post_id": post_id,
                "text": text,
                "link": f"https://t.me/{post_id}"
            })
    return results


def check_and_notify_channels(target_chat_id=None, channels=None):
    """Check channels, find new posts, summarize and notify."""
    if not target_chat_id:
        target_chat_id = CHAT_ID
    if not channels:
        channels = DEFAULT_CHANNELS

    seen = load_seen_posts()
    new_posts_found = 0

    for ch in channels:
        clean_ch = ch.replace("@", "").strip()
        logging.info(f"Checking channel: @{clean_ch}")
        posts = fetch_channel_posts(clean_ch)
        
        channel_seen = seen.get(clean_ch, [])

        # If running for the first time on a channel, register existing posts so we don't spam 20 posts at once
        if not channel_seen and posts:
            logging.info(f"First run for @{clean_ch}. Recording {len(posts)} existing posts.")
            # Summarize only the latest post as a welcome
            latest = posts[-1]
            summary = summarize_text(latest["text"], clean_ch)
            msg = (
                f"🌟 <b>خلاصه آخرین پست کانال @{clean_ch}:</b>\n\n"
                f"{summary}\n\n"
                f"🔗 <a href='{latest['link']}'>مشاهده پست در کانال</a>"
            )
            send_telegram_message(target_chat_id, msg)
            seen[clean_ch] = [p["post_id"] for p in posts]
            new_posts_found += 1
            save_seen_posts(seen)
            continue

        # Look for unseen posts
        for post in posts:
            p_id = post["post_id"]
            if p_id not in channel_seen:
                logging.info(f"New post found in @{clean_ch}: {p_id}")
                summary = summarize_text(post["text"], clean_ch)
                msg = (
                    f"📢 <b>پست جدید از @{clean_ch}:</b>\n\n"
                    f"{summary}\n\n"
                    f"🔗 <a href='{post['link']}'>مشاهده پست در تلگرام</a>"
                )
                send_telegram_message(target_chat_id, msg)
                channel_seen.append(p_id)
                new_posts_found += 1
                time.sleep(2)  # brief pause between sends

        # Keep last 50 posts per channel to keep file size small
        seen[clean_ch] = channel_seen[-50:]

    save_seen_posts(seen)
    logging.info(f"Done channel check. New posts processed: {new_posts_found}")
    return new_posts_found


def get_telegram_updates(offset=None):
    """Fetch new messages from Telegram users."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates?timeout=30"
    if offset:
        url += f"&offset={offset}"
    req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=40) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok"):
                return data.get("result", [])
    except Exception as e:
        logging.error(f"getUpdates error: {e}")
    return []


def handle_user_message(msg):
    """Handle incoming message from user (AI Chat + Commands)."""
    chat_id = msg.get("chat", {}).get("id")
    text = msg.get("text", "").strip()
    forward_text = msg.get("caption") or msg.get("text", "")

    if not chat_id:
        return

    # Check if forwarded message
    if msg.get("forward_date") or msg.get("forward_origin"):
        send_telegram_message(chat_id, "⏳ در حال خلاصه‌سازی پیام فوروارد شده با هوش مصنوعی...")
        summary = summarize_text(forward_text, "پیام فوروارد شده")
        reply = f"📋 <b>خلاصه پیام فوروارد شده:</b>\n\n{summary}"
        send_telegram_message(chat_id, reply)
        return

    # Commands
    if text.startswith("/start"):
        welcome = (
            "سلام! 👋 به ربات هوش مصنوعی خودتان خوش آمدید.\n\n"
            "من کارهای زیر را برای شما انجام می‌دهم:\n"
            "۱. 📰 <b>بررسی و خلاصه کانال‌ها:</b> پست‌های کانال‌های دلخواه شما را خلاصه می‌کنم.\n"
            "۲. 🤖 <b>چت هوش مصنوعی:</b> هر سوالی دارید به زبان فارسی بپرسید تا پاسخ دهم.\n"
            "۳. 🔄 <b>خلاصه پیام دلخواه:</b> هر پیامی را به من فوروارد کنید تا فوراً خلاصه‌اش را تحویلتان دهم.\n\n"
            "دستورات موجود:\n"
            "▫️ <code>/check</code> : بررسی فوری کانال‌ها و ارسال خلاصه پست‌های جدید\n"
            "▫️ <code>/status</code> : نمایش وضعیت ربات"
        )
        send_telegram_message(chat_id, welcome)
        return

    if text.startswith("/check"):
        send_telegram_message(chat_id, "🔍 در حال بررسی کانال‌ها برای دریافت پست‌های جدید...")
        found = check_and_notify_channels(target_chat_id=chat_id)
        if found == 0:
            send_telegram_message(chat_id, "✅ هیچ پست جدیدی یافت نشد. همه پست‌ها قبلاً بررسی شده‌اند.")
        return

    if text.startswith("/status"):
        send_telegram_message(chat_id, "✅ ربات هوش مصنوعی فعال و آماده به کار است!")
        return

    # General AI Chat with Gemini
    system_instruction = (
        "تو یک دستیار هوش مصنوعی فارسی‌زبان، بسیار مودب، دانا، باهوش و خوش‌برخورد هستی. "
        "به زبان فارسی روان و شیوا پاسخ بده. "
        "اگر کاربر سوالی پرسید پاسخ دقیق، کاربردی و ساختاریافته بده."
    )
    send_telegram_message(chat_id, "💭 ...")
    ai_reply = call_gemini(text, system_instruction)
    send_telegram_message(chat_id, ai_reply, parse_mode=None)


def run_continuous_bot():
    """Run bot in continuous long-polling mode (for live AI chat and monitoring)."""
    logging.info("Starting Telegram AI Bot in continuous mode...")
    offset = None
    last_channel_check = 0
    CHANNEL_CHECK_INTERVAL = 300  # Check channels every 5 minutes

    while True:
        try:
            # Check channels periodically
            now = time.time()
            if now - last_channel_check > CHANNEL_CHECK_INTERVAL:
                check_and_notify_channels()
                last_channel_check = now

            # Fetch incoming updates from users
            updates = get_telegram_updates(offset)
            for update in updates:
                offset = update["update_id"] + 1
                if "message" in update:
                    handle_user_message(update["message"])

            time.sleep(1)
        except KeyboardInterrupt:
            logging.info("Bot stopped by user.")
            break
        except Exception as e:
            logging.error(f"Unexpected error in loop: {e}")
            time.sleep(5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Telegram AI Channel Summarizer & Chatbot")
    parser.add_argument("--cron", action="store_true", help="Run once for GitHub Actions cron job")
    args = parser.parse_args()

    if args.cron:
        logging.info("Executing in CRON mode (Channel check and notify)...")
        check_and_notify_channels()
    else:
        run_continuous_bot()
