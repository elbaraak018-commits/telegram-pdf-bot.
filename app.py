import os
import logging
import mimetypes
import time
import asyncio
import threading

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from telegram import Update, error
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from google import genai
from flask import Flask, send_from_directory, request

# ----------------------------------------
# 1. الإعدادات والثوابت
# ----------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096 
MAX_WAIT_TIME = 300 
ADMIN_ID = 1050772765 

# ----------------------------------------
# 2. إعداد قاعدة البيانات PostgreSQL
# ----------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL")
engine = None

if DATABASE_URL:
    try:
        engine = create_engine(DATABASE_URL)
        logging.info("PostgreSQL engine initialized successfully.")
    except Exception as e:
        logging.error(f"Failed to create PostgreSQL engine: {e}")
else:
    logging.warning("DATABASE_URL not set. Database functions will not work.")

def init_db():
    if not engine: return
    try:
        with engine.connect() as connection:
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    is_active INTEGER DEFAULT 1,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    message_content TEXT,
                    message_type TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            connection.commit()
            logging.info("Users and Messages tables ready.")
    except SQLAlchemyError as e:
        logging.error(f"DB init error: {e}")

async def register_user(update: Update):
    if not engine: return
    user = update.effective_user
    init_db()
    insert_query = text("""
        INSERT INTO users (user_id, first_name, username, is_active) 
        VALUES (:user_id, :first_name, :username, 1)
        ON CONFLICT (user_id) DO UPDATE SET 
            first_name = EXCLUDED.first_name,
            username = EXCLUDED.username,
            is_active = 1;
    """)
    try:
        with engine.connect() as connection:
            connection.execute(insert_query, {
                "user_id": user.id,
                "first_name": user.first_name,
                "username": user.username or ''
            })
            connection.commit()
    except SQLAlchemyError as e:
        logging.error(f"Error registering user: {e}")

def log_message(user_id, content, msg_type):
    if not engine: return
    content_to_log = str(content)[:65535]
    query = text("INSERT INTO messages (user_id, message_content, message_type) VALUES (:user_id, :content, :msg_type)")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"user_id": user_id, "content": content_to_log, "msg_type": msg_type})
            connection.commit()
    except SQLAlchemyError as e:
        logging.error(f"Error logging message: {e}")

def update_user_status(user_id, status):
    if not engine: return
    update_query = text("UPDATE users SET is_active = :status WHERE user_id = :user_id")
    try:
        with engine.connect() as connection:
            connection.execute(update_query, {"status": status, "user_id": user_id})
            connection.commit()
    except SQLAlchemyError as e:
        logging.error(f"Error updating user status: {e}")

def split_text(text, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    if len(text) <= max_len: return [text]
    parts = []
    current_part = ""
    lines = text.splitlines(keepends=True)
    for line in lines:
        while len(line) > max_len:
            segment = line[:max_len]
            if current_part:
                parts.append(current_part.strip())
                current_part = "" 
            parts.append(segment.strip())
            line = line[max_len:]
        if len(current_part) + len(line) > max_len:
            if current_part:
                parts.append(current_part.strip())
            current_part = line
        else:
            current_part += line
    if current_part: parts.append(current_part.strip())
    return [p for p in parts if p]

# ----------------------------------------
# 3. إعدادات Gemini
# ----------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

FILE_PROCESSING_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية واسمك EduVise 🌟...
"""

def get_or_create_chat(user_id, context: ContextTypes.DEFAULT_TYPE):
    if not client: return None
    chat_key = f'chat_{user_id}'
    if chat_key not in context.user_data:
        system_instruction = FILE_PROCESSING_PROMPT.replace('**مهمتك المحددة:**', '')
        system_instruction += "\n\n أنت الآن في وضع الرد على النصوص..."
        context.user_data[chat_key] = client.chats.create(
            model='gemini-2.5-flash',
            config={'system_instruction': system_instruction} 
        )
    return context.user_data[chat_key]

# ----------------------------------------
# 4. Handlers
# ----------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    log_message(update.effective_user.id, "/start", 'command')
    welcome_message = """
مرحباً بك 👋
... (نفس الرسالة السابقة)
"""
    await update.message.reply_text(welcome_message)

# هنا تضيف باقي Handlers مثل get_users_command, get_message_logs, broadcast, handle_document, handle_text كما في الكود السابق
# لا حاجة لتغييرها، تعمل كما هي

# ----------------------------------------
# 5. Flask + Webhook
# ----------------------------------------
flask_app = Flask(__name__, static_folder='webapp')
app: Application = None 

@flask_app.route('/')
def serve_webapp():
    return send_from_directory('webapp', 'index.html')

@flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    global app
    if not app: return "Bot Application not initialized", 500
    try:
        data = request.get_json(silent=True)
        if not data: return "No JSON received", 400
        update = Update.de_json(data=data, bot=app.bot)

        def run_async_process(update_obj):
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(app.process_update(update_obj))
            except RuntimeError:
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                new_loop.run_until_complete(app.process_update(update_obj))
            except Exception as e:
                logging.error(f"Error in async thread processing update: {e}")

        thread = threading.Thread(target=run_async_process, args=(update,))
        thread.start()
        return "OK"
    except Exception as e:
        logging.error(f"Error processing Telegram update in Flask route: {e}")
        return "Error", 500

# ----------------------------------------
# 6. Main
# ----------------------------------------
def main():
    global app
    if not BOT_TOKEN or not WEBHOOK_URL:
        print("❌ Bot Token أو Webhook URL مفقود!")
        return

    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة Handlers
    app.add_handler(CommandHandler("start", start_command))
    # أضف باقي CommandHandlers و MessageHandlers هنا كما في الكود الأصلي

    PORT = int(os.environ.get("PORT", 8443))
    webhook_url_full = f"{WEBHOOK_URL}/{BOT_TOKEN}"
    try:
        app.bot.set_webhook(url=webhook_url_full)
        logging.info(f"✅ Telegram Webhook set to: {webhook_url_full}")
    except Exception as e:
        logging.error(f"❌ فشل إعداد Telegram Webhook: {e}")

    print(f"🚀 Starting Flask server on port {PORT}...")
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
