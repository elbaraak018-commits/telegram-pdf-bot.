import os
import logging
import mimetypes
import time 
import asyncio # 💡 إضافة مكتبة asyncio لمعالجة التزامن
import threading # 💡 إضافة مكتبة threading لمعالجة الـ Webhook في خلفية Flask

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from telegram import Update, error
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from google import genai 
# 🛠️ الإضافات لدمج خادم الويب (Flask)
from flask import Flask, send_from_directory, request 
import requests 

# ----------------------------------------
# 1. الإعدادات والثوابت
# ----------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096 
MAX_WAIT_TIME = 300 
# 🔑 يجب تغيير هذا الرقم إلى معرف حساب المدير الخاص بك
ADMIN_ID = 1050772765 

# ----------------------------------------
# 2. إعدادات قاعدة البيانات (PostgreSQL)
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
    """تهيئة قاعدة البيانات وإنشاء جدولي المستخدمين والرسائل في Postgres."""
    if not engine:
        logging.error("Database engine is not available.")
        return

    try:
        with engine.connect() as connection:
            # 1. إنشاء جدول المستخدمين (users)
            connection.execute(text(f"""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    is_active INTEGER DEFAULT 1,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            
            # 2. إنشاء جدول الرسائل (messages)
            connection.execute(text(f"""
                CREATE TABLE IF NOT EXISTS messages (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT REFERENCES users(user_id) ON DELETE CASCADE,
                    message_content TEXT,
                    message_type TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            connection.commit()
            logging.info("Users and Messages tables created or confirmed in PostgreSQL.")
    except OperationalError as e:
        logging.error(f"PostgreSQL connection failed during init: {e}")
    except SQLAlchemyError as e:
        logging.error(f"SQLAlchemy error during init: {e}")


async def register_user(update: Update):
    """تسجيل المستخدم أو تحديث حالته في قاعدة بيانات Postgres."""
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
        logging.error(f"Error registering user in Postgres: {e}")


def log_message(user_id, content, msg_type):
    if not engine: return
    
    # ضمان أن المحتوى لا يتجاوز حجم الحقل في القاعدة
    content_to_log = str(content)[:65535] 
    
    query = text("INSERT INTO messages (user_id, message_content, message_type) VALUES (:user_id, :content, :msg_type)")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"user_id": user_id, "content": content_to_log, "msg_type": msg_type})
            connection.commit()
    except SQLAlchemyError as e:
        logging.error(f"Error logging message: {e}")


def update_user_status(user_id, status):
    """دالة مساعدة لتحديث حالة المستخدم (0 = غير نشط/حظر، 1 = نشط) في Postgres."""
    if not engine: return
    
    update_query = text("UPDATE users SET is_active = :status WHERE user_id = :user_id")
    
    try:
        with engine.connect() as connection:
            connection.execute(update_query, {"status": status, "user_id": user_id})
            connection.commit()
    except SQLAlchemyError as e:
        logging.error(f"Error updating user status in Postgres: {e}")

# ----------------------------------------
# 3. الوظائف المساعدة
# ----------------------------------------
def split_text(text, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    """
    تقسيم النص الطويل إلى أجزاء لا تتجاوز الحد الأقصى (4096 حرفاً)، 
    مع محاولة الحفاظ على فواصل الأسطر والفقرات.
    """
    if len(text) <= max_len:
        return [text]
    
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

    if current_part:
        parts.append(current_part.strip())
        
    return [p for p in parts if p]


# ----------------------------------------
# 4. إعدادات Gemini
# ----------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

FILE_PROCESSING_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية واسمك EduVise 🌟. مهمتك تحليل أي محتوى تعليمي (صورة، فيديو، ملف PDF، إلخ) وتحويله لحزمة دراسية شاملة ومزينة برموز إيموجي مناسبة لكل نقطة لتسهيل القراءة وجعل المظهر جذاباً.

**مهمتك المحددة:**
1.  ابدأ الرد بـ **عنوان الدرس** المناسب للمحتوى، مع إيموجي جذاب.
2.  قدم **الشرح المفصل والملخص** للمحتوى، واستخدم إيموجي 📚 أو 💡 لتنسيق النقاط الرئيسية.
3.  قدم **أمثلة تطبيقية**، واستخدم إيموجي ✏️ أو 🧪.
4.  قدم **مجموعة أسئلة متنوعة** (صح/خطأ، اختيار من متعدد، أكمل، علل)، واستخدم إيموجي ❓ أو 📝.
5.  قدم **الأجوبة النموذجية**، واستخدم إيموجي ✅ أو 💯.

ملاحظة هامة: لا تضف أي مقدمات أو شرح لمهامك أو أي عبارات تشير إلى تقسيم الردود. ابدأ مباشرة بعنوان الدرس والشرح.
"""

def get_or_create_chat(user_id, context: ContextTypes.DEFAULT_TYPE):
    """جلب كائن المحادثة الخاص بالمستخدم أو إنشائه إذا لم يكن موجوداً."""
    if not client:
        return None
        
    chat_key = f'chat_{user_id}'
    
    if chat_key not in context.user_data:
        system_instruction = FILE_PROCESSING_PROMPT.replace('**مهمتك المحددة:**', '')
        system_instruction += "\n\n أنت الآن في وضع الرد على النصوص. مهمتك الرد على المستخدم بناءً على سجل المحادثة السابق. إذا طلب تعديل أو الإشارة إلى شيء سابق، افهم السياق وجاوبه."
        
        context.user_data[chat_key] = client.chats.create(
            model='gemini-2.5-flash',
            config={'system_instruction': system_instruction} 
        )
    
    return context.user_data[chat_key]

# ----------------------------------------
# 5. معالجات الأوامر (Handlers)
# ----------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update) 
    log_message(update.effective_user.id, "/start", 'command') 
    
    welcome_message = """
مرحباً بك 👋

هنا يمكنك إرسال أي ملف PDF، صورة، فيديو، مقطع صوتي أو نص، ليقوم البوت بـ:

• 📄 قراءة وتحليل المحتوى بدقة
• 📝 شرح الدروس والموضوعات بأسلوب مبسّط
• 🎧 تحليل وشرح المقاطع الصوتية
• 📚 إنشاء أمثلة تطبيقية
• 🧩 توليد تمارين مخصّصة
• ❓ طرح أسئلة لفهم أعمق
• ✔️ تقديم الإجابات النموذجية

كل ما عليك هو إرسال الملف أو النص الآن… وسيتكفّل البوت بالباقي! 🚀🤖

ㅤ
ㅤ
Powered by @Albaraa_1
"""
    await update.message.reply_text(welcome_message)

# باقي Handlers وجميع الأكواد كما أرسلتها سابقًا...

# ----------------------------------------
# 9. الدمج والتشغيل (الإصلاح النهائي)
# ----------------------------------------

# 🛠️ تهيئة Flask
flask_app = Flask(__name__, static_folder='webapp')
app: Application = None 

# 1. مسار لخدمة التطبيق المصغر (Mini App)
@flask_app.route('/')
def serve_webapp():
    """خدمة ملف index.html عندما يفتح المستخدم التطبيق المصغر."""
    return send_from_directory('webapp', 'index.html')

# 2. مسار Webhook تيليجرام
@flask_app.route(f"/{BOT_TOKEN}", methods=["POST"])
def telegram_webhook():
    """معالج Webhook الذي يستقبل تحديثات تيليجرام ويرسلها إلى معالجات البوت."""
    global app
    
    if not app:
        return "Bot Application not initialized", 500
    
    try:
        data = request.get_json(force=True)
        if not data:
            return "No JSON received", 400

        update = Update.de_json(data=data, bot=app.bot)

        # تشغيل المعالجة في Thread منفصل
        def run_async_process(update_obj):
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                loop.run_until_complete(app.process_update(update_obj))
            except Exception as e:
                logging.error(f"Error processing update in thread: {e}")

        threading.Thread(target=run_async_process, args=(update,)).start()

        # الرد فورًا على Telegram لتجنب انتهاء المهلة
        return "OK"
        
    except Exception as e:
        logging.error(f"Error in webhook route: {e}")
        return "Error", 500

def main():
    global app
    
    if not BOT_TOKEN or not WEBHOOK_URL:
        print("❌ Bot Token أو Webhook URL مفقود! يرجى إعداد المتغيرات البيئية.")
        return

    init_db() 
    
    # إعداد تطبيق تيليجرام (Handlers)
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command)) 
    app.add_handler(CommandHandler("messages_log", get_message_logs))
    app.add_handler(CommandHandler("clean_logs", clean_logs_command))
    app.add_handler(broadcast_handler) 
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)) 

    PORT = int(os.environ.get("PORT", 8443))
    print(f"Bot and WebApp are ready to serve on port {PORT}...")
    
    # إعداد Webhook عند بدء التشغيل
    webhook_url_full = f"{WEBHOOK_URL}/{BOT_TOKEN}"
    try:
        app.bot.set_webhook(url=webhook_url_full)
        logging.info(f"✅ Telegram Webhook set to: {webhook_url_full}")
    except Exception as e:
        logging.error(f"❌ فشل إعداد Telegram Webhook: {e}")

    # تشغيل خادم Flask لخدمة الـ Mini App واستقبال Webhook
    print(f"🚀 Starting Flask server to handle requests...")
    flask_app.run(host="0.0.0.0", port=PORT)

if __name__ == "__main__":
    main()
