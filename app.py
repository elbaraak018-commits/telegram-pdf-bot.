import os
import logging
import mimetypes
import time 
import base64
import json
import datetime
import re
import fitz  # مكتبة PyMuPDF لقراءة ملفات الـ PDF
import requests # مكتبة لتحميل الخط تلقائياً
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from telegram import Update, error, ReplyKeyboardMarkup, ReplyKeyboardRemove, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, 
    CommandHandler, 
    MessageHandler, 
    filters, 
    ContextTypes, 
    ConversationHandler,
    CallbackQueryHandler
)
from groq import Groq 

# إضافات لتحويل النص إلى PDF مع دعم العربية
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from arabic_reshaper import reshape
from bidi.algorithm import get_display
import textwrap

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
MAX_FILE_SIZE = 20 * 1024 * 1024  # 20 ميجابايت كحد أقصى

BOT_TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# إعداد نظام تدوير المفاتيح
raw_keys = os.getenv("GROQ_API_KEYS", "") 
GROQ_API_KEYS = [k.strip() for k in raw_keys.split(",") if k.strip()]
current_key_index = 0

def get_groq_client():
    global current_key_index
    if not GROQ_API_KEYS:
        logger.critical("GROQ_API_KEYS is missing!")
        return None
    return Groq(api_key=GROQ_API_KEYS[current_key_index])

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

# --- دالة مساعدة لضمان وجود الخط العربي ---
def ensure_arabic_font():
    font_filename = "Amiri-Regular.ttf"
    if not os.path.exists(font_filename):
        try:
            url = "https://github.com/google/fonts/raw/main/ofl/amiri/Amiri-Regular.ttf"
            response = requests.get(url)
            with open(font_filename, "wb") as f:
                f.write(response.content)
            logger.info("Downloaded Amiri font successfully.")
        except Exception as e:
            logger.error(f"Failed to download font: {e}")
            return None
    return font_filename

# --- دالة استخراج عنوان الدرس ---
def extract_lesson_title(text_content):
    try:
        match = re.search(r"(?:عنوان الدرس|العنوان)[:\s\-]*([^\n\r]+)", text_content)
        if match:
            title = match.group(1).strip()
            clean_title = re.sub(r'[\\/*?:"<>|]', "", title)
            return clean_title[:50]
        
        first_line = text_content.strip().split('\n')[0]
        if len(first_line) < 60 and "EduVise" not in first_line:
             clean_title = re.sub(r'[\\/*?:"<>|]', "", first_line)
             return clean_title
    except Exception:
        pass
    return "ملخص_شامل"

def create_pdf_from_text(content, base_filename="EduVise_Explanation.pdf"):
    lesson_title = extract_lesson_title(content)
    timestamp = int(time.time())
    filename = f"{lesson_title}_{timestamp}.pdf"

    try:
        c = canvas.Canvas(filename, pagesize=A4)
        width, height = A4
        font_path = ensure_arabic_font()
        font_name = 'ArabicFont'
        
        if font_path:
            try:
                pdfmetrics.registerFont(TTFont(font_name, font_path))
                c.setFont(font_name, 14)
            except Exception as e:
                logger.error(f"Font registration failed: {e}")
                c.setFont("Helvetica", 12)
                font_name = "Helvetica"
        else:
            c.setFont("Helvetica", 12)
            font_name = "Helvetica"
        
        lines = content.split('\n')
        y = height - 50
        margin_right = 50
        max_width = width - 100
        
        for line in lines:
            if not line.strip():
                y -= 20
                continue
            try:
                reshaped_text = reshape(line)
                bidi_text = get_display(reshaped_text)
            except:
                bidi_text = line
            
            if font_name == 'ArabicFont':
                wrapped_lines = textwrap.wrap(bidi_text, width=70) 
            else:
                wrapped_lines = textwrap.wrap(line, width=80)

            for w_line in wrapped_lines:
                if y < 50:
                    c.showPage()
                    c.setFont(font_name, 14)
                    y = height - 50
                if font_name == 'ArabicFont':
                    c.drawRightString(width - margin_right, y, w_line)
                else:
                    c.drawString(margin_right, y, w_line)
                y -= 25
        c.save()
        return filename
    except Exception as e:
        logger.error(f"Error creating PDF: {e}")
        return None

# ------------------------------------------------------------------------------
# 4. محرك الذكاء الاصطناعي (تم تحديث البرومبت لضمان الشمولية)
# ------------------------------------------------------------------------------

FILE_PROCESSING_PROMPT = """
أنت EduVise 🌟، خبير تعليمي محترف. مهمتك تحليل المحتوى المرفق بدقة متناهية وشاملة.
تنبيه هام جداً: إذا كان الملف يحتوي على "عدة دروس" أو "عدة مواضيع"، يجب عليك شرح "كل درس على حدة" بالتفصيل الممل. 
لا تكتفِ بشرح الدرس الأول فقط، بل استخرج كل المعلومات من بداية النص وحتى نهايته.

نسق الإجابة لكل درس كالتالي:
1. 📌 عنوان الجزء/الدرس: (حدد اسم الدرس الفرعي).
2. 📖 الشرح التفصيلي: (اشرح كل نقطة في هذا الجزء بأسلوب مبسط ومطول).
3. 💡 ملخص الأفكار لهذا الجزء.
4. ✏️ أمثلة تطبيقية.
... كرر هذا النمط لكل موضوع موجود في النص المرفق.

في نهاية الرد:
5. 📝 بنك أسئلة شامل: (أسئلة تغطي كافة الدروس التي شرحتها).
6. ✅ الأجوبة النموذجية.

يجب أن يكون الرد "موسوعياً" ولا تختصر أي معلومة مهما كانت صغيرة.
"""

CHAT_PROMPT = """
أنت EduVise 🌟، مساعد ذكي، ودود، واحترافي. 
- رد على المستخدم بأسلوب لبق ومميز.
- استخدم الإيموجيات بشكل متوازن.
- إذا سألك المستخدم سؤالاً عاماً، أجب بذكاء واختصار مفيد.
- لا تتبع نظام التحليل الدراسي إلا إذا أرسل المستخدم ملفاً أو طلب "شرح درس".
- منشئ وصانع ومصمم ومطور هذا البوت هو Al-baraa.
"""

def get_ai_response(content, mode="text", history=None, media_path=None):
    global current_key_index
    retries = len(GROQ_API_KEYS)

    for attempt in range(retries):
        client = get_groq_client()
        if not client: return "⚠️ عذراً، محرك الذكاء الاصطناعي غير متوفر حالياً."

        try:
            if mode == "vision" and media_path:
                base64_image = encode_image(media_path)
                response = client.chat.completions.create(
                    model="llama-3.2-90b-vision-instant", 
                    messages=[{"role": "user", "content": [
                        {"type": "text", "text": FILE_PROCESSING_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]}],
                    temperature=0.6, max_tokens=4096
                )
                return response.choices[0].message.content
            elif mode == "audio_transcribe" and media_path:
                with open(media_path, "rb") as audio_file:
                    transcription = client.audio.transcriptions.create(
                        file=(media_path, audio_file.read()),
                        model="whisper-large-v3",
                        response_format="text"
                    )
                return transcription
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
            error_msg = str(e)
            if "429" in error_msg or "rate_limit_exceeded" in error_msg:
                current_key_index = (current_key_index + 1) % len(GROQ_API_KEYS)
                continue 
            return f"❌ حدث خطأ: {error_msg}"

# ------------------------------------------------------------------------------
# 5. أوامر المدير (Admin Control Panel)
# ------------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    log_message(update.effective_user.id, "/start", 'command')
    welcome_text = (
        "مرحباً بك في <b>EduVise</b> 👋🌟\n\n"
        "أنا مساعدك الدراسي الشامل. أرسل لي أي ملف (حتى لو كان يحتوي على دروس كثيرة) وسأقوم بتحليله بالكامل.\n\n"
        "• 📄 <b>تحليل ملفات PDF بجميع صفحاتها</b>\n"
        "• 🖼️ <b>شرح الصور والرسوم البيانية</b>\n"
        "• 🎧 <b>تلخيص المقاطع الصوتية</b>\n"
        "• 📝 <b>شرح الدروس بأسلوب مفصل جداً</b>\n\n"
        "Powered by @Albaraa_1"
    )
    await update.message.reply_text(welcome_text, parse_mode='HTML')

async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            total_users = connection.execute(text('SELECT COUNT(*) FROM users')).scalar()
            users_list = connection.execute(text('SELECT first_name, username FROM users ORDER BY join_date DESC LIMIT 50')).fetchall()
        response = f"<b>👥 الإجمالي: {total_users}</b>\n"
        for fn, un in users_list: response += f"👤 {fn} (@{un})\n"
        await update.message.reply_text(response, parse_mode='HTML')
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def get_message_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            logs = connection.execute(text("SELECT message_content FROM messages ORDER BY timestamp DESC LIMIT 20")).fetchall()
        response = "<b>📜 آخر 20 رسالة:</b>\n"
        for row in logs: response += f"- {row[0][:50]}\n"
        await update.message.reply_text(response, parse_mode='HTML')
    except Exception as e: await update.message.reply_text(f"❌ {e}")

async def clean_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    try:
        with engine.connect() as connection:
            connection.execute(text("DELETE FROM messages"))
            connection.commit()
            await update.message.reply_text("✅ تم التنظيف.")
    except Exception as e: await update.message.reply_text(f"❌ {e}")

# ------------------------------------------------------------------------------
# 6. نظام البث (Broadcast System)
# ------------------------------------------------------------------------------
BROADCAST_STATE = 1
async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID: return
    await update.message.reply_text("🎙️ أرسل رسالة البث.")
    return BROADCAST_STATE

async def broadcast_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    with engine.connect() as conn:
        users = [r[0] for r in conn.execute(text("SELECT user_id FROM users WHERE is_active = 1")).fetchall()]
    for uid in users:
        try: await context.bot.copy_message(chat_id=uid, from_chat_id=update.effective_chat.id, message_id=update.message.message_id)
        except: update_user_status(uid, 0)
    await update.message.reply_text("✅ تم البث.")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("❌ تم الإلغاء.")
    return ConversationHandler.END

# ------------------------------------------------------------------------------
# 7. معالج الوسائط الشامل (Unified Media Handler) - تم تحديثه لمعالجة نصوص أطول
# ------------------------------------------------------------------------------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    msg = update.message
    temp_path = None
    
    file_size = 0
    if msg.document: file_size = msg.document.file_size
    elif msg.video: file_size = msg.video.file_size
    elif msg.audio: file_size = msg.audio.file_size
    elif msg.voice: file_size = msg.voice.file_size
    elif msg.photo: file_size = msg.photo[-1].file_size

    if file_size > MAX_FILE_SIZE:
        await msg.reply_text("⚠️ الملف كبير جداً (الأقصى 20MB).")
        return

    status = await msg.reply_text("⏳ جاري استخراج المحتوى وتحليله بشكل كامل...")
    try:
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.jpg"
            mode = "vision"
        elif msg.video or msg.voice or msg.audio:
            file_obj = await (msg.video or msg.voice or msg.audio).get_file()
            temp_path = f"temp_{file_obj.file_unique_id}"
            mode = "audio_choice"
        elif msg.document and msg.document.mime_type == "application/pdf":
            file_obj = await msg.document.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.pdf"
            mode = "pdf"
        else:
            await status.edit_text("❌ نوع ملف غير مدعوم.")
            return

        await file_obj.download_to_drive(temp_path)

        if mode == "pdf":
            doc = fitz.open(temp_path)
            # استخراج النص من كل الصفحات لضمان عدم ضياع أي درس
            extracted_text = "".join([page.get_text() for page in doc])
            doc.close()
            
            if not extracted_text.strip():
                await status.edit_text("⚠️ الملف لا يحتوي على نص قابل للقراءة.")
                return
            
            # زيادة الحد الأقصى للنص المرسل للذكاء الاصطناعي إلى 60 ألف حرف لتغطية عدة دروس
            ai_reply = get_ai_response(f"قم بشرح كافة المواضيع والدروس الواردة في هذا النص شرحاً وافياً ومفصلاً:\n{extracted_text[:60000]}", mode="study_text")
            
            pdf_file = create_pdf_from_text(ai_reply)
            await status.delete()
            if pdf_file:
                await msg.reply_document(document=open(pdf_file, 'rb'), caption="✅ تم شرح كافة دروس الملف بالتفصيل! إليك الملف.")
                os.remove(pdf_file)
            else: await msg.reply_text(ai_reply)

        elif mode == "vision":
            ai_reply = get_ai_response(None, mode="vision", media_path=temp_path)
            pdf_file = create_pdf_from_text(ai_reply)
            await status.delete()
            if pdf_file:
                await msg.reply_document(document=open(pdf_file, 'rb'), caption="✅ تم شرح الصورة.")
                os.remove(pdf_file)
            else: await msg.reply_text(ai_reply)

        elif mode == "audio_choice":
            transcription = get_ai_response(None, mode="audio_transcribe", media_path=temp_path)
            context.user_data[f"audio_text_{msg.from_user.id}"] = transcription
            keyboard = [[InlineKeyboardButton("نص فقط 📝", callback_data="audio_show_text")],
                        [InlineKeyboardButton("شرح كامل (PDF) 🧠", callback_data="audio_explain_text")]]
            await status.delete()
            await msg.reply_text("✅ تم تجهيز المقطع الصوتي، اختر الإجراء:", reply_markup=InlineKeyboardMarkup(keyboard))

    except Exception as e:
        logger.error(f"Error: {e}")
        await status.edit_text("❌ حدث خطأ أثناء المعالجة.")
    finally:
        if temp_path and os.path.exists(temp_path): os.remove(temp_path)

# ------------------------------------------------------------------------------
# 8. معالج أزرار الصوت (Audio Callback Handler)
# ------------------------------------------------------------------------------
async def audio_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    user_id = query.from_user.id
    data = query.data
    await query.answer()

    transcription = context.user_data.get(f"audio_text_{user_id}")
    if not transcription:
        await query.edit_message_text("⚠️ انتهت الجلسة.")
        return

    if data == "audio_show_text":
        for part in split_text(transcription): await query.message.reply_text(part)
    elif data == "audio_explain_text":
        status_msg = await query.message.reply_text("⏳")
        ai_reply = get_ai_response(f"اشرح هذا المحتوى الصوتي شرحاً مفصلاً وشاملاً:\n{transcription}", mode="study_text")
        pdf_file = create_pdf_from_text(ai_reply)
        await status_msg.delete()
        if pdf_file:
            await query.message.reply_document(document=open(pdf_file, 'rb'), caption="✅ تحليل المقطع الصوتي.")
            os.remove(pdf_file)
        else: await query.message.reply_text(ai_reply)

# ------------------------------------------------------------------------------
# 9. معالج النصوص والدردشة (Text & Chat Handler)
# ------------------------------------------------------------------------------
async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update)
    user_id = update.effective_user.id
    user_input = update.message.text
    log_message(user_id, user_input, 'text')

    study_keywords = ["اشرح", "لخص", "حلل", "درس", "شرح"]
    is_study_request = any(k in user_input.lower() for k in study_keywords)
    mode = "study_text" if is_study_request else "text"

    history_key = f"hist_{user_id}"
    if history_key not in context.user_data: context.user_data[history_key] = []
    session_history = context.user_data[history_key]
    
    msg_wait = await update.message.reply_text("🤔")
    try:
        ai_reply = get_ai_response(user_input, mode=mode, history=session_history)
        session_history.append({"role": "user", "content": user_input})
        session_history.append({"role": "assistant", "content": ai_reply})
        context.user_data[history_key] = session_history[-8:] 

        await msg_wait.delete()
        if is_study_request:
            pdf_file = create_pdf_from_text(ai_reply)
            if pdf_file:
                await update.message.reply_document(document=open(pdf_file, 'rb'), caption="✅ الشرح التفصيلي جاهز.")
                os.remove(pdf_file)
            else: await update.message.reply_text(ai_reply)
        else:
            for part in split_text(ai_reply):
                await update.message.reply_text(part)
    except Exception: await msg_wait.edit_text("⚠️ خطأ في المعالجة.")

# ------------------------------------------------------------------------------
# 10. الوظائف الأساسية والتشغيل (Main Runner)
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
    app.add_handler(CallbackQueryHandler(audio_callback_handler, pattern="^audio_"))
    app.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.AUDIO | filters.VOICE | filters.Document.ALL, media_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    if WEBHOOK_URL:
        PORT = int(os.environ.get("PORT", 8443))
        app.run_webhook(listen="0.0.0.0", port=PORT, url_path=BOT_TOKEN, webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}")
    else:
        app.run_polling()

if __name__ == "__main__":
    main()
