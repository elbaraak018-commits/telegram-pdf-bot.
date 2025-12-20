# ==============================================================================
# اسم المشروع: EduVise AI Bot - النسخة الاحترافية الشاملة (المحدثة)
# المطور الأصلي: @Albaraa_1
# الوصف: بوت تعليمي ذكي يدعم (نصوص، صور، فيديو، صوت، PDF) باستخدام Groq و Postgres
# ==============================================================================

import os
import logging
import mimetypes
import time 
import base64
import json
import datetime
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

# الثوابت الأساسية
MAX_TELEGRAM_MESSAGE_LENGTH = 4096 
MAX_FILE_SIZE_MB = 20 # حد أقصى للملفات لتجنب تعليق السيرفر
ADMIN_ID = 1050772765  

# جلب مفاتيح الوصول من متغيرات النظام
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# تهيئة عميل Groq
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
        logger.error(f"Database Engine Error: {e}")

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
        logger.error(f"Init DB Error: {e}")

async def register_user(update: Update):
    if not engine or not update.effective_user: return
    user = update.effective_user
    query = text("""
        INSERT INTO users (user_id, first_name, username, is_active) 
        VALUES (:user_id, :first_name, :username, 1)
        ON CONFLICT (user_id) DO UPDATE SET is_active = 1;
    """)
    try:
        with engine.connect() as connection:
            connection.execute(query, {"user_id": user.id, "first_name": user.first_name, "username": user.username or ''})
            connection.commit()
    except SQLAlchemyError as e: logger.error(f"Register Error: {e}")

def log_message(user_id, content, msg_type):
    if not engine: return
    try:
        with engine.connect() as connection:
            connection.execute(text("INSERT INTO messages (user_id, message_content, message_type) VALUES (:user_id, :content, :msg_type)"),
                               {"user_id": user_id, "content": str(content)[:5000], "msg_type": msg_type})
            connection.commit()
    except SQLAlchemyError as e: logger.error(f"Log Error: {e}")

# ------------------------------------------------------------------------------
# 3. الأدوات المساعدة (Utility Functions)
# ------------------------------------------------------------------------------
def split_text(text_to_split, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    if not text_to_split: return []
    return [text_to_split[i:i+max_len] for i in range(0, len(text_to_split), max_len)]

def encode_image(image_path):
    with open(image_path, "rb") as img:
        return base64.b64encode(img.read()).decode('utf-8')

# ------------------------------------------------------------------------------
# 4. محرك الذكاء الاصطناعي (Groq AI Engine)
# ------------------------------------------------------------------------------

# برومبت تحليل الملفات (صور، بي دي اف، صوت)
FILE_PROCESSING_PROMPT = """أنت معلم خبير. حلل المحتوى المرفق وقدم: 1. عنوان جذاب، 2. شرح مفصل بإيموجي، 3. أمثلة، 4. أسئلة متنوعة، 5. الأجوبة النموذجية."""

# برومبت الدردشة العادية (رد مباشر)
CHAT_PROMPT = "أنت مساعد ذكي ومثقف اسمك EduVise. أجب على أسئلة المستخدم بذكاء ولباقة دون الحاجة لتحويل الكلام إلى درس تعليمي إلا إذا طلب منك ذلك."

def get_ai_response(content, mode="text", history=None, media_path=None):
    if not client: return "⚠️ عذراً، محرك الذكاء الاصطناعي غير مفعل."
    try:
        if mode == "vision" and media_path:
            response = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[{"role": "user", "content": [{"type": "text", "text": FILE_PROCESSING_PROMPT}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encode_image(media_path)}"}}]}],
                temperature=0.7
            )
            return response.choices[0].message.content

        elif mode == "audio" and media_path:
            with open(media_path, "rb") as f:
                trans = client.audio.transcriptions.create(file=(media_path, f.read()), model="whisper-large-v3", response_format="text")
            return get_ai_response(f"حلل المحتوى التالي تعليمياً:\n{trans}", mode="file_text")

        else:
            # تمييز بين الرد كشات (نموذج ذكاء اصطناعي) وبين تحليل الملفات
            sys_prompt = CHAT_PROMPT if mode == "text" else FILE_PROCESSING_PROMPT
            messages = [{"role": "system", "content": sys_prompt}]
            if history: messages.extend(history)
            messages.append({"role": "user", "content": content})

            response = client.chat.completions.create(model="llama-3.3-70b-versatile", messages=messages, temperature=0.8)
            return response.choices[0].message.content

    except Exception as e:
        logger.error(f"AI Error: {e}")
        return "❌ نعتذر، واجهنا مشكلة في معالجة طلبك عبر خوادمنا الذكية."

# ------------------------------------------------------------------------------
# 5. أوامر المدير المطورة (Enhanced Admin UI)
# ------------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    welcome = (
        "✨ **أهلاً بك في EduVise AI** ✨\n\n"
        "أنا مساعدك الذكي المتطور. يمكنك:\n"
        "💬 **الدردشة معي:** أرسل أي سؤال وسأجيبك فوراً.\n"
        "📂 **تحليل الملفات:** أرسل (صورة، صوت، PDF) لتحويلها لدرس.\n\n"
        "🚀 ابدأ بكتابة أي شيء الآن!"
    )
    await update.message.reply_text(welcome, parse_mode='Markdown')

async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as conn:
            total = conn.execute(text("SELECT COUNT(*) FROM users")).scalar()
            last_users = conn.execute(text("SELECT first_name, username, join_date FROM users ORDER BY join_date DESC LIMIT 10")).fetchall()

        res = f"📊 **إحصائيات النظام**\n"
        res += f"━━━━━━━━━━━━━━━\n"
        res += f"👤 إجمالي المستخدمين: `{total}`\n\n"
        res += f"🆕 **آخر المنضمين:**\n"
        for fn, un, jd in last_users:
            res += f"• {fn} (@{un or 'N/A'}) - _{jd.strftime('%m/%d')}_\n"
        
        await update.message.reply_text(res, parse_mode='Markdown')
    except Exception as e: await update.message.reply_text(f"❌ خطأ: {e}")

async def get_message_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as conn:
            logs = conn.execute(text("SELECT u.first_name, m.message_type, m.timestamp FROM messages m JOIN users u ON m.user_id = u.user_id ORDER BY m.timestamp DESC LIMIT 15")).fetchall()

        res = "📜 **آخر التفاعلات الحية**\n━━━━━━━━━━━━━━━\n"
        for name, mtype, ts in logs:
            res += f"🕒 `{ts.strftime('%H:%M')}` | **{name[:10]}** ➔ `{mtype}`\n"
        
        await update.message.reply_text(res, parse_mode='Markdown')
    except Exception as e: await update.message.reply_text(f"❌ خطأ: {e}")

# ------------------------------------------------------------------------------
# 7. معالج الوسائط (Media Handler with Error Handling)
# ------------------------------------------------------------------------------

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    msg = update.message
    
    # تحقق من حجم الملف (تقديري)
    if msg.document and msg.document.file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await msg.reply_text(f"⚠️ الملف كبير جداً! الحد الأقصى هو {MAX_FILE_SIZE_MB} ميجابايت.")
        return

    status = await msg.reply_text("⏳ جاري المعالجة... قد يستغرق الأمر لحظات.")
    temp_path = None
    try:
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            temp_path = f"t_{file_obj.file_unique_id}.jpg"
            mode = "vision"
        elif msg.voice or msg.audio:
            file_obj = await (msg.voice or msg.audio).get_file()
            temp_path = f"t_{file_obj.file_unique_id}.mp3"
            mode = "audio"
        elif msg.document and msg.document.mime_type == "application/pdf":
            file_obj = await msg.document.get_file()
            temp_path = f"t_{file_obj.file_unique_id}.pdf"
            mode = "pdf"
        else: return

        await file_obj.download_to_drive(temp_path)

        if mode == "pdf":
            doc = fitz.open(temp_path)
            extracted = "".join([page.get_text() for page in doc])
            doc.close()
            if len(extracted.strip()) < 10: raise ValueError("الملف فارغ أو غير مقروء")
            ai_reply = get_ai_response(extracted[:12000], mode="file_text")
        elif mode == "vision":
            ai_reply = get_ai_response(None, mode="vision", media_path=temp_path)
        elif mode == "audio":
            ai_reply = get_ai_response(None, mode="audio", media_path=temp_path)

        await status.delete()
        for part in split_text(ai_reply):
            await msg.reply_text(part)

    except Exception as e:
        logger.error(f"Media Error: {e}")
        await status.edit_text(f"❌ خطأ فني: {str(e)}")
    finally:
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

# ------------------------------------------------------------------------------
# 8. معالج النصوص (AI Chat Mode)
# ------------------------------------------------------------------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    user_id = update.effective_user.id
    user_input = update.message.text
    
    log_message(user_id, user_input, 'text')
    
    # ذاكرة الجلسة
    history_key = f"hist_{user_id}"
    if history_key not in context.user_data: context.user_data[history_key] = []
    
    thinking = await update.message.reply_text("💭")
    
    try:
        # هنا البوت يرد كـ "نموذج ذكاء اصطناعي" مباشر
        ai_reply = get_ai_response(user_input, mode="text", history=context.user_data[history_key])
        
        # حفظ السياق
        context.user_data[history_key].append({"role": "user", "content": user_input})
        context.user_data[history_key].append({"role": "assistant", "content": ai_reply})
        context.user_data[history_key] = context.user_data[history_key][-6:]

        await thinking.delete()
        for part in split_text(ai_reply):
            await update.message.reply_text(part)
            
    except Exception as e:
        await thinking.edit_text("❌ عذراً، تعذر الوصول للمحرك الذكي حالياً.")

# ------------------------------------------------------------------------------
# 9. التشغيل
# ------------------------------------------------------------------------------

def main():
    if not BOT_TOKEN: return
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command))
    app.add_handler(CommandHandler("messages_log", get_message_logs))
    app.add_handler(MessageHandler(filters.PHOTO | filters.AUDIO | filters.VOICE | filters.Document.PDF, media_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if WEBHOOK_URL:
        app.run_webhook(listen="0.0.0.0", port=int(os.environ.get("PORT", 8443)), url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
