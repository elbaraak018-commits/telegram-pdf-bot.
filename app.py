import os
import logging
import mimetypes
import time 
import base64
import json
import datetime
import re
import fitz  # مكتبة PyMuPDF لقراءة ملفات الـ PDF
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from telegram import Update, error, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes, 
    ConversationHandler
)
from groq import Groq 

# ------------------------------------------------------------------------------
# 1. إعدادات السجلات والبيئة (Logging & Environment)
# ------------------------------------------------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

MAX_TELEGRAM_MESSAGE_LENGTH = 4000 
ADMIN_ID = 1050772765 

BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not GROQ_API_KEY:
    logger.critical("GROQ_API_KEY is missing!")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

# ------------------------------------------------------------------------------
# 2. إدارة قاعدة البيانات (PostgreSQL Management)
# ------------------------------------------------------------------------------
engine = None
if DATABASE_URL:
    try:
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
    except Exception as e:
        logger.error(f"Failed to create PostgreSQL engine: {e}")

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
    except Exception as e:
        logger.error(f"Database init error: {e}")

async def register_user(update: Update):
    if not engine or not update.effective_user: return
    user = update.effective_user
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
                "user_id": user.id, "first_name": user.first_name, "username": user.username or ''
            })
            connection.commit()
    except SQLAlchemyError as e:
        logger.error(f"Register user error: {e}")

def log_message(user_id, content, msg_type):
    if not engine: return
    content_to_log = str(content)[:65535] 
    query = text("INSERT INTO messages (user_id, message_content, message_type) VALUES (:user_id, :content, :msg_type)")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"user_id": user_id, "content": content_to_log, "msg_type": msg_type})
            connection.commit()
    except SQLAlchemyError as e:
        logger.error(f"Log message error: {e}")

def update_user_status(user_id, status):
    if not engine: return
    query = text("UPDATE users SET is_active = :status WHERE user_id = :user_id")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"status": status, "user_id": user_id})
            connection.commit()
    except SQLAlchemyError as e:
        logger.error(f"Update status error: {e}")

# ------------------------------------------------------------------------------
# 3. الأدوات المساعدة (Utility Functions)
# ------------------------------------------------------------------------------
def split_text(text, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    if len(text) <= max_len: return [text]
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        split_at = text.rfind('\n', 0, max_len)
        if split_at == -1: split_at = max_len
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return parts

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# ------------------------------------------------------------------------------
# 4. محرك الذكاء الاصطناعي (Groq AI Engine)
# ------------------------------------------------------------------------------

# برومبت تحليل الملفات (شامل ومقسم)
FILE_PROCESSING_PROMPT = """
أنت EduVise 🌟، خبير تعليمي محترف. مهمتك تحليل المحتوى المرفق بدقة متناهية.
يجب أن يكون ردك "كاملاً جداً" ولا تختصر أي معلومة. 

نسق الإجابة كالتالي:
1. 📌 عنوان الدرس: (عنوان جذاب).
2. 📖 الشرح التفصيلي: (اشرح كل نقطة بالتفصيل الممل وبأسلوب مبسط مع استخدام إيموجيات تعليمية).
3. 💡 ملخص الأفكار: (نقاط مركزة للأهم).
4. ✏️ أمثلة توضيحية: (أمثلة تطبيقية شاملة).
5. 📝 بنك الأسئلة: (أسئلة متنوعة: مقالية، اختيارية، صح وخطأ).
6. ✅ الأجوبة النموذجية: (حلول مفصلة لجميع الأسئلة).

تنبيه: إذا كان المحتوى طويلاً، استمر في الشرح حتى تغطي كل ذرة معلومات فيه.
"""

# برومبت الدردشة العامة
CHAT_PROMPT = """
أنت EduVise 🌟، مساعد ذكي، ودود، واحترافي. 
- رد على المستخدم بأسلوب لبق ومميز.
- استخدم الكثير من الإيموجيات المناسبة لجعل المحادثة حيوية.
- إذا سألك المستخدم سؤالاً عاماً، أجب بذكاء واختصار مفيد.
- لا تتبع نظام التحليل الدراسي (العنوان، الشرح، الأسئلة) إلا إذا أرسل المستخدم ملفاً أو طلب منك "شرح درس" صراحة.
- اجعل شخصيتك كصديق متعلم ومحفز.
"""

def get_ai_response(content, mode="text", history=None, media_path=None):
    if not client: return "⚠️ عذراً، محرك الذكاء الاصطناعي غير متوفر حالياً."
    try:
        if mode == "vision" and media_path:
            base64_image = encode_image(media_path)
            response = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": FILE_PROCESSING_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]}],
                temperature=0.6, max_tokens=4096
            )
            return response.choices[0].message.content
        elif mode == "audio" and media_path:
            with open(media_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=(media_path, audio_file.read()),
                    model="whisper-large-v3",
                    response_format="text"
                )
            return get_ai_response(f"حلل هذا المحتوى الصوتي بشكل كامل وشامل:\n{transcription}", mode="study_text")
        else:
            system_p = CHAT_PROMPT if mode == "text" else FILE_PROCESSING_PROMPT
            messages = [{"role": "system", "content": system_p}]
            if history: messages.extend(history)
            messages.append({"role": "user", "content": content})
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages, temperature=0.7, max_tokens=4096
            )
            return response.choices[0].message.content
    except Exception as e:
        logger.error(f"Groq AI Error: {e}")
        return f"❌ حدث خطأ أثناء معالجة الطلب: {str(e)}"

# ------------------------------------------------------------------------------
# 5. أوامر المدير (Admin Control Panel)
# ------------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    log_message(update.effective_user.id, "/start", 'command')
    welcome_text = (
        "مرحباً بك في **EduVise** 👋🌟\n\n"
        "أنا مساعدك الدراسي الذكي. أرسل لي أي ملف أو نص وسأقوم بـ:\n\n"
        "• 📄 تحليل ملفات PDF بدقة\n"
        "• 🖼️ شرح الصور والرسوم البيانية\n"
        "• 🎧 تلخيص المقاطع الصوتية والفيديو\n"
        "• 📝 شرح الدروس بأسلوب مبسط\n"
        "• 🧩 إنشاء تمارين وأسئلة مخصصة\n\n"
        "أرسل ملفك الآن لنبدأ! 🚀\n\n"
        "Powered by @Albaraa_1"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            total_users = connection.execute(text('SELECT COUNT(*) FROM users')).scalar()
            active_users = connection.execute(text('SELECT COUNT(*) FROM users WHERE is_active = 1')).scalar()
            users_list = connection.execute(text('SELECT user_id, first_name, username FROM users ORDER BY join_date DESC LIMIT 40')).fetchall()

        response = f"👥 إحصائيات المستخدمين:\n"
        response += f"┃ الإجمالي: {total_users}\n"
        response += f"┃ النشطون: {active_users}\n"
        response += "┃ 📋 آخر المسجلين:\n"
        response += "┗━━━━━━━━━━━━━━━\n\n"
        
        for user_id, first_name, username in users_list:
            uname = f"@{username}" if username else "NoUser"
            response += f"👤 {first_name[:15]} | {uname} | ID: {user_id}\n"

        for part in split_text(response):
            await update.message.reply_text(part)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

async def get_message_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            logs = connection.execute(text("""
                SELECT timestamp, message_content, message_type, users.first_name
                FROM messages JOIN users ON messages.user_id = users.user_id
                ORDER BY timestamp DESC LIMIT 20; 
            """)).fetchall()

        response = "📜 آخر 20 تفاعل:\n┗━━━━━━━━━━━━━━━\n\n"
        for timestamp, content, msg_type, first_name in logs:
            content_preview = content[:40].replace('\n', ' ')
            response += f"🕒 {timestamp.strftime('%H:%M')} | {first_name[:10]}\n🔹 {msg_type}: {content_preview}...\n───\n"

        await update.message.reply_text(response)
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في السجلات: {e}")

async def clean_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            connection.execute(text("DELETE FROM messages"))
            connection.commit()
            await update.message.reply_text("✅ تم تنظيف السجلات بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

# ------------------------------------------------------------------------------
# 6. نظام البث (Broadcast System)
# ------------------------------------------------------------------------------
BROADCAST_STATE = 1

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🎙️ أرسل رسالة البث الآن أو /cancel.")
    return BROADCAST_STATE

async def broadcast_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with engine.connect() as conn:
        users = [r[0] for r in conn.execute(text("SELECT user_id FROM users WHERE is_active = 1")).fetchall()]
    msg = await update.message.reply_text(f"⏳ جاري البث...")
    success, fail = 0, 0
    for uid in users:
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
            success += 1
            time.sleep(0.05)
        except:
            fail += 1
            update_user_status(uid, 0)
    await msg.edit_text(f"✅ انتهى البث:\n- نجاح: {success}\n- فشل: {fail}")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء البث.")
    return ConversationHandler.END

# ------------------------------------------------------------------------------
# 7. معالج الوسائط الشامل (Unified Media Handler)
# ------------------------------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    msg = update.message
    status = await msg.reply_text("⏳")
    temp_path = None
    try:
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.jpg"
            mode = "vision"
        elif msg.video:
            file_obj = await msg.video.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.mp4"
            mode = "audio"
        elif msg.voice:
            file_obj = await msg.voice.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.ogg"
            mode = "audio"
        elif msg.audio:
            file_obj = await msg.audio.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.mp3"
            mode = "audio"
        elif msg.document and msg.document.mime_type == "application/pdf":
            file_obj = await msg.document.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.pdf"
            mode = "pdf"
        else:
            await status.edit_text("❌ غير مدعوم.")
            return

        await file_obj.download_to_drive(temp_path)
        log_message(msg.from_user.id, f"File: {mode}", mode)

        if mode == "pdf":
            await status.edit_text("📖 جاري قراءة PDF وتحليله بالكامل...")
            doc = fitz.open(temp_path)
            extracted_text = "".join([page.get_text() for page in doc])
            doc.close()
            ai_reply = get_ai_response(f"حلل هذا الملف التعليمي كاملاً:\n{extracted_text[:15000]}", mode="study_text")
        elif mode == "vision":
            await status.edit_text("👁️ جاري تحليل الصورة تعليمياً...")
            ai_reply = get_ai_response(None, mode="vision", media_path=temp_path)
        elif mode == "audio":
            await status.edit_text("🎧 جاري معالجة الصوت...")
            ai_reply = get_ai_response(None, mode="audio", media_path=temp_path)

        await status.delete()
        for part in split_text(ai_reply):
            await msg.reply_text(part)
            
    except Exception as e:
        await status.edit_text(f"❌ خطأ: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

# ------------------------------------------------------------------------------
# 8. معالج النصوص والدردشة (Text & Chat Handler)
# ------------------------------------------------------------------------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    user_id = update.effective_user.id
    user_input = update.message.text
    log_message(user_id, user_input, 'text')

    # الكلمات المفتاحية التي تحول البوت لنظام "الشرح الدراسي" بدلاً من الدردشة
    study_keywords = ["اشرح لي", "ما هو شرح", "لخص درس", "حلل النص", "اعطني اسئلة"]
    mode = "study_text" if any(k in user_input.lower() for k in study_keywords) else "text"

    history_key = f"hist_{user_id}"
    if history_key not in context.user_data: context.user_data[history_key] = []
    session_history = context.user_data[history_key]
    
    msg_wait = await update.message.reply_text("💡")
    try:
        ai_reply = get_ai_response(user_input, mode=mode, history=session_history)
        session_history.append({"role": "user", "content": user_input})
        session_history.append({"role": "assistant", "content": ai_reply})
        context.user_data[history_key] = session_history[-8:] 

        await msg_wait.delete()
        for part in split_text(ai_reply):
            await update.message.reply_text(part)
            
    except Exception as e:
        await msg_wait.edit_text(f"⚠️ خطأ: {e}")

# ------------------------------------------------------------------------------
# 9. الوظائف الأساسية والتشغيل (Main Runner)
# ------------------------------------------------------------------------------
def main():
    if not BOT_TOKEN: return
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command))
    app.add_handler(CommandHandler("messages_log", get_message_logs))
    app.add_handler(CommandHandler("clean_logs", clean_logs_command))

    app.add_handler(ConversationHandler(
        entry_points=[CommandHandler('broadcast', broadcast_start)],
        states={BROADCAST_STATE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_execute)]},
        fallbacks=[CommandHandler('cancel', broadcast_cancel)]
    ))

    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.PDF, media_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if WEBHOOK_URL:
        PORT = int(os.environ.get("PORT", 8443))
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
