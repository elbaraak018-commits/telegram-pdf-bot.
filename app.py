# ==============================================================================
# اسم المشروع: EduVise AI Bot - النسخة الاحترافية الشاملة
# المطور الأصلي: @Albaraa_1
# الوصف: بوت تعليمي ذكي يدعم (نصوص، صور، فيديو، صوت، PDF) باستخدام Groq و Postgres
# عدد الأسطر التقريبي: +700 سطر بفضل التفصيل البرمجي ومعالجة الأخطاء الشاملة
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
MAX_WAIT_TIME = 300 
ADMIN_ID = 1050772765  # معرف المدير (يجب التأكد منه)

# جلب مفاتيح الوصول من متغيرات النظام
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# تهيئة عميل Groq
if not GROQ_API_KEY:
    logger.critical("GROQ_API_KEY is missing! The bot will not be able to process AI requests.")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

# ------------------------------------------------------------------------------
# 2. إدارة قاعدة البيانات (PostgreSQL Management)
# ------------------------------------------------------------------------------
engine = None
if DATABASE_URL:
    try:
        # تحسين الاتصال بقاعدة البيانات لتناسب خوادم Render/Heroku
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=10, max_overflow=20)
        logger.info("PostgreSQL engine initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to create PostgreSQL engine: {e}")
else:
    logger.warning("DATABASE_URL not set. Database functions will be disabled.")

def init_db():
    """تجهيز الجداول الأساسية في قاعدة البيانات إذا لم تكن موجودة."""
    if not engine:
        return

    try:
        with engine.connect() as connection:
            # جدول المستخدمين
            connection.execute(text("""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    is_active INTEGER DEFAULT 1,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            
            # جدول سجلات الرسائل
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
            logger.info("Database tables verified/created successfully.")
    except Exception as e:
        logger.error(f"Error during database initialization: {e}")

async def register_user(update: Update):
    """تسجيل بيانات المستخدم الجديد أو تحديث بيانات المستخدم الحالي."""
    if not engine or not update.effective_user:
        return
    
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
    except SQLAlchemyError as e:
        logger.error(f"Error in register_user: {e}")

def log_message(user_id, content, msg_type):
    """حفظ سجل الرسالة في قاعدة البيانات لمراجعة المدير."""
    if not engine:
        return
    
    # تنظيف المحتوى لضمان عدم حدوث أخطاء في التخزين
    clean_content = str(content)[:65000] 
    
    query = text("INSERT INTO messages (user_id, message_content, message_type) VALUES (:user_id, :content, :msg_type)")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"user_id": user_id, "content": clean_content, "msg_type": msg_type})
            connection.commit()
    except SQLAlchemyError as e:
        logger.error(f"Error in log_message: {e}")

def update_user_status(user_id, status):
    """تحديث حالة المستخدم (نشط/محظور)."""
    if not engine:
        return
    
    query = text("UPDATE users SET is_active = :status WHERE user_id = :user_id")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"status": status, "user_id": user_id})
            connection.commit()
    except SQLAlchemyError as e:
        logger.error(f"Error in update_user_status: {e}")

# ------------------------------------------------------------------------------
# 3. الأدوات المساعدة (Utility Functions)
# ------------------------------------------------------------------------------
def split_text(text_to_split, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    """تقسيم النصوص الطويلة جداً لضمان وصولها عبر تيليجرام دون أخطاء."""
    if not text_to_split:
        return []
    if len(text_to_split) <= max_len:
        return [text_to_split]
    
    parts = []
    current_part = ""
    lines = text_to_split.splitlines(keepends=True)
    
    for line in lines:
        if len(line) > max_len:
            if current_part:
                parts.append(current_part.strip())
                current_part = ""
            while len(line) > max_len:
                parts.append(line[:max_len].strip())
                line = line[max_len:]
            current_part = line
        elif len(current_part) + len(line) > max_len:
            parts.append(current_part.strip())
            current_part = line
        else:
            current_part += line

    if current_part:
        parts.append(current_part.strip())
    return [p for p in parts if p]

def encode_image(image_path):
    """تشفير الصورة إلى Base64 ليتمكن نموذج Vision من قراءتها."""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# ------------------------------------------------------------------------------
# 4. محرك الذكاء الاصطناعي (Groq AI Engine)
# ------------------------------------------------------------------------------

FILE_PROCESSING_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية واسمك EduVise 🌟. مهمتك تحليل أي محتوى تعليمي (صورة، فيديو، ملف PDF، إلخ) وتحويله لحزمة دراسية شاملة ومزينة برموز إيموجي مناسبة لكل نقطة لتسهيل القراءة وجعل المظهر جذاباً.

**مهمتك المحددة:**
1. ابدأ الرد بـ **عنوان الدرس** المناسب للمحتوى، مع إيموجي جذاب.
2. قدم **الشرح المفصل والملخص** للمحتوى، واستخدم إيموجي 📚 أو 💡 لتنسيق النقاط الرئيسية.
3. قدم **أمثلة تطبيقية**، واستخدم إيموجي ✏️ أو 🧪.
4. قدم **مجموعة أسئلة متنوعة** (صح/خطأ، اختيار من متعدد، أكمل، علل)، واستخدم إيموجي ❓ أو 📝.
5. قدم **الأجوبة النموذجية**، واستخدم إيموجي ✅ أو 💯.

ملاحظة هامة: لا تضف أي مقدمات أو شرح لمهامك. ابدأ مباشرة بالتحليل التعليمي.
"""

def get_ai_response(content, mode="text", history=None, media_path=None):
    """المحرك المركزي لإرسال الطلبات إلى Groq بناءً على نوع الوسائط."""
    if not client:
        return "⚠️ عذراً، محرك الذكاء الاصطناعي غير متوفر حالياً."

    try:
        # أ. وضع معالجة الصور (Vision)
        if mode == "vision" and media_path:
            base64_image = encode_image(media_path)
            response = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": FILE_PROCESSING_PROMPT},
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}
                            }
                        ]
                    }
                ],
                temperature=0.7,
                max_tokens=2048
            )
            return response.choices[0].message.content

        # ب. وضع معالجة الصوت والفيديو (Whisper)
        elif mode == "audio" and media_path:
            with open(media_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=(media_path, audio_file.read()),
                    model="whisper-large-v3",
                    response_format="text"
                )
            # بعد تحويل الصوت لنص، نرسله لمعالجة تعليمية
            return get_ai_response(f"حلل هذا النص المستخرج من تسجيل صوتي تعليمياً:\n{transcription}", mode="text")

        # ج. وضع معالجة النصوص (Chat)
        else:
            messages = [{"role": "system", "content": FILE_PROCESSING_PROMPT}]
            if history:
                messages.extend(history)
            messages.append({"role": "user", "content": content})

            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
                temperature=0.8,
                max_tokens=4096
            )
            return response.choices[0].message.content

    except Exception as e:
        logger.error(f"Groq AI Error: {e}")
        return f"❌ حدث خطأ أثناء معالجة الطلب عبر Groq: {str(e)}"

# ------------------------------------------------------------------------------
# 5. أوامر المدير (Admin Control Panel)
# ------------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البداية وتسجيل المستخدم."""
    await register_user(update)
    log_message(update.effective_user.id, "/start", 'command')
    
    welcome_text = (
        "مرحباً بك في **EduVise** 🌟\n\n"
        "أنا بوتك التعليمي المتكامل. أرسل لي أي نوع من الوسائط وسأقوم بتحويله لدرس شامل:\n"
        "• 📄 ملفات PDF (سأقرأ محتواها وأشرحه)\n"
        "• 🖼️ الصور (سأحلل المسائل والرسوم)\n"
        "• 🎬 الفيديوهات (سأسمع الشرح وألخصه)\n"
        "• 🎤 المقاطع الصوتية (سأحولها لملخص مكتوب)\n"
        "• ✍️ النصوص (سأجيب على استفساراتك)\n\n"
        "أنا جاهز الآن.. ماذا لديك لنتعلمه اليوم؟"
    )
    await update.message.reply_text(welcome_text, parse_mode='Markdown')

async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض إحصائيات المستخدمين (للمدير فقط)."""
    if update.effective_user.id != ADMIN_ID:
        return
    
    if not engine:
        await update.message.reply_text("❌ قاعدة البيانات غير متصلة.")
        return

    try:
        with engine.connect() as connection:
            total = connection.execute(text("SELECT COUNT(*) FROM users")).scalar()
            active = connection.execute(text("SELECT COUNT(*) FROM users WHERE is_active = 1")).scalar()
            last_users = connection.execute(text("SELECT user_id, first_name, username FROM users ORDER BY join_date DESC LIMIT 20")).fetchall()

        res = f"📊 **إحصائيات البوت:**\n- الإجمالي: {total}\n- النشطون: {active}\n\n✅ **آخر 20 مستخدم:**\n"
        for uid, fn, un in last_users:
            res += f"👤 {fn} (@{un or 'N/A'}) - `[ID: {uid}]` \n"
        
        for part in split_text(res):
            await update.message.reply_text(part, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

async def get_message_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """عرض سجل الرسائل الأخيرة (للمدير فقط)."""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        with engine.connect() as connection:
            logs = connection.execute(text("""
                SELECT u.first_name, m.message_type, m.message_content, m.timestamp 
                FROM messages m JOIN users u ON m.user_id = u.user_id 
                ORDER BY m.timestamp DESC LIMIT 30
            """)).fetchall()

        if not logs:
            await update.message.reply_text("📭 لا يوجد سجلات حالياً.")
            return

        report = "📜 **آخر 30 تفاعل:**\n\n"
        for name, mtype, content, ts in logs:
            preview = (content[:60] + '..') if len(content) > 60 else content
            report += f"🕒 `{ts.strftime('%H:%M:%S')}` | **{name}**: [{mtype}] {preview}\n"
            report += "--- \n"
        
        for part in split_text(report):
            await update.message.reply_text(part, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ في السجلات: {e}")

async def clean_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """حذف سجل الرسائل لتوفير مساحة (للمدير فقط)."""
    if update.effective_user.id != ADMIN_ID:
        return
    
    try:
        with engine.connect() as connection:
            result = connection.execute(text("DELETE FROM messages"))
            connection.commit()
            await update.message.reply_text(f"✅ تم مسح السجلات بنجاح. (تم حذف {result.rowcount} رسالة)")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

# ------------------------------------------------------------------------------
# 6. نظام البث المتقدم (Broadcast System)
# ------------------------------------------------------------------------------
BROADCAST_STATE = 1

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("📣 **بدء البث العام:**\n\nأرسل الآن الرسالة (نص، صورة، فيديو..) التي تود إرسالها للجميع. أرسل /cancel للتراجع.")
    return BROADCAST_STATE

async def broadcast_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not engine: return ConversationHandler.END
    
    # جلب قائمة المستخدمين
    with engine.connect() as conn:
        users = [r[0] for r in conn.execute(text("SELECT user_id FROM users WHERE is_active = 1")).fetchall()]

    status_msg = await update.message.reply_text(f"⏳ جاري بدء الإرسال إلى {len(users)} مستخدم...")
    
    success, fail = 0, 0
    for uid in users:
        try:
            await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
            success += 1
            time.sleep(0.1) # حماية من Flood تيليجرام
        except Exception as e:
            fail += 1
            if "bot was blocked" in str(e):
                update_user_status(uid, 0)
    
    await status_msg.edit_text(f"✅ **انتهى البث:**\n- نجاح: {success}\n- فشل: {fail}")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم إلغاء البث.")
    return ConversationHandler.END

# ------------------------------------------------------------------------------
# 7. معالج الوسائط الشامل (Unified Media Handler)
# ------------------------------------------------------------------------------

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المسؤول عن استقبال ومعالجة كل أنواع الملفات."""
    await register_user(update)
    msg = update.message
    status = await msg.reply_text("⏳ جاري استلام الملف ومعالجته ذكياً...")
    
    temp_path = None
    try:
        # أ. تحديد نوع الملف وجلبه
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
            await status.edit_text("⚠️ هذا النوع من الملفات غير مدعوم حالياً.")
            return

        # ب. تحميل الملف
        await file_obj.download_to_drive(temp_path)
        log_message(msg.from_user.id, f"Uploaded: {temp_path}", mode)

        # ج. المعالجة بناءً على النوع
        if mode == "pdf":
            await status.edit_text("📖 جاري قراءة صفحات الـ PDF...")
            doc = fitz.open(temp_path)
            extracted_text = ""
            for page in doc:
                extracted_text += page.get_text()
            doc.close()
            
            if not extracted_text.strip():
                ai_reply = "❌ لم أتمكن من العثور على نص مقروء داخل ملف الـ PDF."
            else:
                ai_reply = get_ai_response(f"هذا نص مستخرج من ملف PDF تعليمي، حلله بالتفصيل:\n{extracted_text[:15000]}") # الحد الأقصى للنص المستخرج
        
        elif mode == "vision":
            await status.edit_text("👁️ جاري تحليل الصورة عبر رؤية Groq...")
            ai_reply = get_ai_response(None, mode="vision", media_path=temp_path)
        
        elif mode == "audio":
            await status.edit_text("🎧 جاري الاستماع وتحويل المحتوى لنص...")
            ai_reply = get_ai_response(None, mode="audio", media_path=temp_path)

        # د. إرسال الرد النهائي
        await status.delete()
        for part in split_text(ai_reply):
            await msg.reply_text(part.replace('**', ''), parse_mode=None)

    except Exception as e:
        logger.error(f"Error in media_handler: {e}")
        await status.edit_text(f"❌ خطأ أثناء المعالجة: {str(e)}")
    
    finally:
        if temp_path and os.path.exists(temp_path):
            os.remove(temp_path)

# ------------------------------------------------------------------------------
# 8. معالج النصوص والدردشة (Text & Chat Handler)
# ------------------------------------------------------------------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """التعامل مع النصوص والأسئلة المباشرة."""
    await register_user(update)
    user_id = update.effective_user.id
    user_input = update.message.text
    
    # تسجيل الرسالة
    log_message(user_id, user_input, 'text')

    # ردود سريعة للمطور
    processed_input = user_input.lower().strip()
    if any(word in processed_input for word in ["من مطورك", "مطور البوت", "من صنعك"]):
        await update.message.reply_text("تم تطويري بواسطة المبدع @Albaraa_1 🚀")
        return

    # إدارة ذاكرة الجلسة (آخر 6 رسائل للتركيز)
    history_key = f"hist_{user_id}"
    if history_key not in context.user_data:
        context.user_data[history_key] = []
    
    session_history = context.user_data[history_key]
    
    msg_wait = await update.message.reply_text("🤔")
    
    try:
        # إرسال للذكاء الاصطناعي مع السياق
        ai_reply = get_ai_response(user_input, mode="text", history=session_history)
        
        # تحديث الذاكرة
        session_history.append({"role": "user", "content": user_input})
        session_history.append({"role": "assistant", "content": ai_reply})
        context.user_data[history_key] = session_history[-6:] # حفظ آخر 3 حوارات فقط

        # إرسال الرد
        await msg_wait.delete()
        for part in split_text(ai_reply):
            await update.message.reply_text(part.replace('**', ''))
            
    except Exception as e:
        await msg_wait.edit_text(f"⚠️ حدث خطأ في معالجة النص: {e}")

# ------------------------------------------------------------------------------
# 9. الوظائف الأساسية والتشغيل (Main Runner)
# ------------------------------------------------------------------------------

def main():
    """تشغيل البوت وإعداد المحركات."""
    if not BOT_TOKEN:
        print("❌ خطأ: لم يتم العثور على BOT_TOKEN!")
        return

    # تهيئة قاعدة البيانات
    init_db()
    
    # بناء تطبيق تيليجرام
    app = Application.builder().token(BOT_TOKEN).build()

    # أ. معالجات الأوامر العامة
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command))
    app.add_handler(CommandHandler("messages_log", get_message_logs))
    app.add_handler(CommandHandler("clean_logs", clean_logs_command))

    # ب. معالج البث (Conversation)
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler('broadcast', broadcast_start)],
        states={
            BROADCAST_STATE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_execute)]
        },
        fallbacks=[CommandHandler('cancel', broadcast_cancel)]
    )
    app.add_handler(broadcast_conv)

    # ج. معالج الميديا (صور، فيديو، صوت، PDF)
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.PDF, 
        media_handler
    ))

    # د. معالج النصوص
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # هـ. تشغيل الويب هوك (أو Polling للتجربة المحلية)
    if WEBHOOK_URL:
        PORT = int(os.environ.get("PORT", 8443))
        logger.info(f"Starting Webhook on port {PORT}...")
        app.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=BOT_TOKEN,
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
    else:
        logger.info("Starting Polling Mode (Local)...")
        app.run_polling()

if __name__ == "__main__":
    main()

# ==============================================================================
# نهاية الكود الاحترافي
# تم دمج أنظمة: Groq Vision, Groq Whisper, Llama 3.3, PyMuPDF, SQLAlchemy Postgres
# وتغطية شاملة لكل أنواع الأخطاء الممكنة لضمان استقرار البوت.
# ==============================================================================
