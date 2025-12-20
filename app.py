import os
import logging
import mimetypes
import time 
import base64
import json
import datetime
import re
import fitz  # PyMuPDF
import html

# استيراد مكتبات قاعدة البيانات
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError

# استيراد مكتبات Telegram Bot API
from telegram import (
    Update, 
    error, 
    ReplyKeyboardMarkup, 
    ReplyKeyboardRemove,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes, 
    ConversationHandler
)

# استيراد محرك Groq AI
from groq import Groq 

# ------------------------------------------------------------------------------
# 2. إعدادات السجلات والبيئة
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
    logger.critical("⚠️ GROQ_API_KEY مفقود!")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

# ------------------------------------------------------------------------------
# 3. إدارة قاعدة البيانات
# ------------------------------------------------------------------------------

engine = None
if DATABASE_URL:
    try:
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        
        engine = create_engine(
            DATABASE_URL, 
            pool_pre_ping=True, 
            pool_size=10, 
            max_overflow=20
        )
    except Exception as e:
        logger.error(f"❌ فشل اتصال قاعدة البيانات: {e}")

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
            logger.info("✅ تم تهيئة قاعدة البيانات.")
    except Exception as e:
        logger.error(f"❌ خطأ تهيئة القاعدة: {e}")

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
                "user_id": user.id, 
                "first_name": user.first_name, 
                "username": user.username or ''
            })
            connection.commit()
    except Exception as e:
        logger.error(f"❌ خطأ تسجيل مستخدم: {e}")

def log_message(user_id, content, msg_type):
    if not engine: return
    content_to_log = str(content)[:65535] 
    query = text("""
        INSERT INTO messages (user_id, message_content, message_type) 
        VALUES (:user_id, :content, :msg_type)
    """)
    try:
        with engine.connect() as connection:
            connection.execute(query, {"user_id": user_id, "content": content_to_log, "msg_type": msg_type})
            connection.commit()
    except Exception as e:
        logger.error(f"❌ خطأ حفظ السجل: {e}")

def update_user_status(user_id, status):
    if not engine: return
    query = text("UPDATE users SET is_active = :status WHERE user_id = :user_id")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"status": status, "user_id": user_id})
            connection.commit()
    except Exception as e:
        logger.error(f"❌ خطأ تحديث الحالة: {e}")

# ------------------------------------------------------------------------------
# 4. الأدوات المساعدة
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

def escape_markdown(text):
    """تنظيف النصوص من الرموز التي تكسر تنسيق ماركداون"""
    return text.replace('_', '\\_').replace('*', '\\*').replace('`', '\\`').replace('[', '\\[')

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# ------------------------------------------------------------------------------
# 5. محرك الذكاء الاصطناعي
# ------------------------------------------------------------------------------

FILE_PROCESSING_PROMPT = """أنت EduVise 🌟، خبير تعليمي. قم بتحليل المحتوى المرفق شرحاً مفصلاً مع بنك أسئلة وأجوبة نموذجية."""
CHAT_PROMPT = """أنت EduVise 🌟، مساعد ذكي وودود."""

def get_ai_response(content, mode="text", history=None, media_path=None):
    if not client: return "⚠️ محرك الذكاء الاصطناعي غير متوفر."
    try:
        max_response_tokens = 8000 if mode != "vision" else 4000
        if mode == "vision" and media_path:
            base64_image = encode_image(media_path)
            response = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[{"role": "user", "content": [
                    {"type": "text", "text": FILE_PROCESSING_PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]}],
                temperature=0.6, max_tokens=4000
            )
            return response.choices[0].message.content
        elif mode == "audio" and media_path:
            with open(media_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=(media_path, audio_file.read()), model="whisper-large-v3", response_format="text"
                )
            return get_ai_response(f"حلل هذا المحتوى الصوتي بشمول:\n{transcription}", mode="study_text")
        else:
            system_p = CHAT_PROMPT if mode == "text" else FILE_PROCESSING_PROMPT
            messages = [{"role": "system", "content": system_p}]
            if history: messages.extend(history)
            messages.append({"role": "user", "content": content})
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile", messages=messages, temperature=0.7, max_tokens=max_response_tokens
            )
            return response.choices[0].message.content
    except Exception as e:
        return f"❌ خطأ تقني: {str(e)}"

# ------------------------------------------------------------------------------
# 6. أوامر البوت المعدلة
# ------------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إرسال رسالة الترحيب أولاً ثم التسجيل في القاعدة"""
    welcome_text = (
        "مرحباً بك في **EduVise AI** النسخة المطورة 🌟👋\n\n"
        "أنا مدرسك الخصوصي والذكي، جاهز لمساعدتك في أي وقت!\n\n"
        "**ماذا يمكنني أن أفعل لك؟**\n"
        "• 📄 **تحليل PDF:** أرسل لي أي ملف وسأشرحه لك بالكامل.\n"
        "• 🖼️ **شرح الصور:** صور أي صفحة في كتابك وسأقوم بتحليلها.\n"
        "• 🎧 **تلخيص الصوت:** أرسل مقاطع صوتية أو محاضرات وسألخصها.\n"
        "• 🎥 **الفيديو:** يمكنني استخراج الفائدة من مقاطع الفيديو.\n"
        "• 📝 **كتابة الدروس:** اطلب مني شرح أي موضوع علمي.\n\n"
        "أرسل لي أي شيء الآن وسأبهرك بدقتي! 🚀\n\n"
        "--- \n"
        "المطور: @Albaraa_1"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')
    # التسجيل في القاعدة
    await register_user(update)
    log_message(update.effective_user.id, "/start", 'command')

async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            total = connection.execute(text('SELECT COUNT(*) FROM users')).scalar()
            active = connection.execute(text('SELECT COUNT(*) FROM users WHERE is_active = 1')).scalar()
            last_users = connection.execute(text("SELECT user_id, first_name, username FROM users ORDER BY join_date DESC LIMIT 30")).fetchall()

        msg = f"📊 **إحصائيات البوت:**\n- الإجمالي: {total}\n- النشطون: {active}\n\n"
        msg += "📋 **آخر 30 عضو:**\n"
        for uid, name, uname in last_users:
            safe_name = escape_markdown(name or "Unknown")
            safe_uname = f"@{escape_markdown(uname)}" if uname else "بدون يوزر"
            msg += f"👤 {safe_name} | {safe_uname} | `{uid}`\n"

        for part in split_text(msg):
            await update.message.reply_text(part, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ فشل جلب البيانات: {str(e)}")

async def get_message_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إجمالي الرسائل وآخر 50 رسالة"""
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            total_msgs = connection.execute(text('SELECT COUNT(*) FROM messages')).scalar()
            logs = connection.execute(text("""
                SELECT m.timestamp, m.message_content, m.message_type, u.first_name
                FROM messages m JOIN users u ON m.user_id = u.user_id
                ORDER BY m.timestamp DESC LIMIT 50; 
            """)).fetchall()

        msg = f"📊 **إجمالي الرسائل المسجلة: {total_msgs}**\n\n"
        msg += "📜 **آخر 50 تفاعل في البوت:**\n\n"
        for ts, content, mtype, name in logs:
            time_str = ts.strftime('%H:%M')
            safe_name = escape_markdown(name or "User")
            preview = escape_markdown(str(content)[:45] + '..' if len(str(content)) > 45 else str(content))
            msg += f"🕒 {time_str} | **{safe_name}**\nنوع: {mtype} | محتوى: {preview}\n"
            msg += "-------------------\n"

        for part in split_text(msg):
            await update.message.reply_text(part, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ فشل جلب السجلات: {e}")

async def clean_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            connection.execute(text("DELETE FROM messages"))
            connection.commit()
            await update.message.reply_text("✅ تم مسح السجل.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

# ------------------------------------------------------------------------------
# 7. نظام البث
# ------------------------------------------------------------------------------

BROADCAST_STATE = 1

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🎙️ أرسل رسالة البث أو /cancel.")
    return BROADCAST_STATE

async def broadcast_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with engine.connect() as conn:
        users = [r[0] for r in conn.execute(text("SELECT user_id FROM users WHERE is_active = 1")).fetchall()]
    status_msg = await update.message.reply_text(f"⏳ جاري البث لـ {len(users)}...")
    success, fail = 0, 0
    for uid in users:
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
            success += 1
            time.sleep(0.05)
        except:
            fail += 1
            update_user_status(uid, 0)
    await status_msg.edit_text(f"✅ اكتمل: نجاح {success} | فشل {fail}")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END

# ------------------------------------------------------------------------------
# 8. معالجات الميديا والنصوص
# ------------------------------------------------------------------------------

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    msg = update.message
    status = await msg.reply_text("⏳ جاري المعالجة...")
    temp_path = None
    try:
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.jpg"
            mode = "vision"
        elif msg.document and msg.document.mime_type == "application/pdf":
            file_obj = await msg.document.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.pdf"
            mode = "pdf"
        elif msg.voice or msg.audio:
            file_obj = await (msg.voice or msg.audio).get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.ogg"
            mode = "audio"
        else:
            await status.edit_text("❌ غير مدعوم.")
            return

        await file_obj.download_to_drive(temp_path)
        if mode == "pdf":
            doc = fitz.open(temp_path)
            full_text = "".join([doc[i].get_text() for i in range(min(len(doc), 50))])
            doc.close()
            ai_reply = get_ai_response(f"شرح PDF:\n{full_text[:35000]}", mode="study_text")
        else:
            ai_reply = get_ai_response(None, mode=mode, media_path=temp_path)

        await status.delete()
        for part in split_text(ai_reply): await msg.reply_text(part)
    except Exception as e:
        await status.edit_text(f"❌ خطأ: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    user_input = update.message.text
    log_message(update.effective_user.id, user_input, 'text')
    
    history_key = f"hist_{update.effective_user.id}"
    if history_key not in context.user_data: context.user_data[history_key] = []
    
    msg_wait = await update.message.reply_text("💡")
    ai_reply = get_ai_response(user_input, history=context.user_data[history_key][-10:])
    
    context.user_data[history_key].append({"role": "user", "content": user_input})
    context.user_data[history_key].append({"role": "assistant", "content": ai_reply})
    
    await msg_wait.delete()
    for part in split_text(ai_reply): await update.message.reply_text(part)

# ------------------------------------------------------------------------------
# 10. التشغيل
# ------------------------------------------------------------------------------

def main():
    if not BOT_TOKEN: return
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command))
    app.add_handler(CommandHandler("messages_log", get_message_logs))
    app.add_handler(CommandHandler("clean_logs", clean_logs_command))

    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler('broadcast', broadcast_start)],
        states={BROADCAST_STATE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_execute)]},
        fallbacks=[CommandHandler('cancel', broadcast_cancel)]
    )
    app.add_handler(broadcast_conv)

    media_filters = (filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.PDF)
    app.add_handler(MessageHandler(media_filters, media_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if WEBHOOK_URL:
        PORT = int(os.environ.get("PORT", 8443))
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
