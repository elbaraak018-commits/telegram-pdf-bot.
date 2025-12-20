import logging
import mimetypes
import time 
import base64
import json
import datetime
import re
import fitz  # مكتبة PyMuPDF لقراءة ملفات الـ PDF

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
# 2. إعدادات السجلات والبيئة (Logging & Environment)
# ------------------------------------------------------------------------------

# إعداد السجلات لمراقبة أداء البوت وتتبع الأخطاء
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# الثوابت الأساسية
MAX_TELEGRAM_MESSAGE_LENGTH = 4000 
ADMIN_ID = 1050772765 

# جلب مفاتيح التشغيل من بيئة النظام
BOT_TOKEN = os.getenv("BOT_TOKEN")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
DATABASE_URL = os.getenv("DATABASE_URL")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

# التحقق من وجود مفتاح API لمحرك الذكاء الاصطناعي
if not GROQ_API_KEY:
    logger.critical("⚠️ خطأ: GROQ_API_KEY غير موجود في إعدادات النظام!")
    client = None
else:
    client = Groq(api_key=GROQ_API_KEY)

# ------------------------------------------------------------------------------
# 3. إدارة قاعدة البيانات (PostgreSQL Management)
# ------------------------------------------------------------------------------

engine = None
if DATABASE_URL:
    try:
        # تصحيح رابط قاعدة البيانات ليتوافق مع SQLAlchemy
        if DATABASE_URL.startswith("postgres://"):
            DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
        
        # إنشاء اتصال مع قاعدة البيانات مع إعدادات الـ Pooling
        engine = create_engine(
            DATABASE_URL, 
            pool_pre_ping=True, 
            pool_size=10, 
            max_overflow=20
        )
    except Exception as e:
        logger.error(f"❌ فشل إنشاء اتصال قاعدة البيانات: {e}")

def init_db():
    """تهيئة الجداول الأساسية في قاعدة البيانات إذا لم تكن موجودة"""
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
            
            # جدول الرسائل والسجلات
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
            logger.info("✅ تم تهيئة قاعدة البيانات بنجاح.")
    except Exception as e:
        logger.error(f"❌ خطأ أثناء تهيئة قاعدة البيانات: {e}")

async def register_user(update: Update):
    """تسجيل المستخدم الجديد أو تحديث بيانات المستخدم الحالي"""
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
        logger.error(f"❌ خطأ في تسجيل المستخدم {user.id}: {e}")

def log_message(user_id, content, msg_type):
    """حفظ سجل الرسائل في قاعدة البيانات"""
    if not engine: 
        return
        
    content_to_log = str(content)[:65535] 
    query = text("""
        INSERT INTO messages (user_id, message_content, message_type) 
        VALUES (:user_id, :content, :msg_type)
    """)
    
    try:
        with engine.connect() as connection:
            connection.execute(query, {
                "user_id": user_id, 
                "content": content_to_log, 
                "msg_type": msg_type
            })
            connection.commit()
    except SQLAlchemyError as e:
        logger.error(f"❌ خطأ في حفظ السجل: {e}")

def update_user_status(user_id, status):
    """تحديث حالة المستخدم (نشط/غير نشط) خاصة عند حظر البوت"""
    if not engine: 
        return
        
    query = text("UPDATE users SET is_active = :status WHERE user_id = :user_id")
    try:
        with engine.connect() as connection:
            connection.execute(query, {"status": status, "user_id": user_id})
            connection.commit()
    except SQLAlchemyError as e:
        logger.error(f"❌ خطأ في تحديث حالة المستخدم: {e}")

# ------------------------------------------------------------------------------
# 4. الأدوات المساعدة (Utility Functions)
# ------------------------------------------------------------------------------

def split_text(text, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    """تقسيم النصوص الطويلة جداً إلى أجزاء لتجنب قيود تيليجرام"""
    if len(text) <= max_len: 
        return [text]
        
    parts = []
    while text:
        if len(text) <= max_len:
            parts.append(text)
            break
        # البحث عن أقرب سطر جديد للتقسيم بشكل جميل
        split_at = text.rfind('\n', 0, max_len)
        if split_at == -1: 
            split_at = max_len
            
        parts.append(text[:split_at].strip())
        text = text[split_at:].strip()
    return parts

def encode_image(image_path):
    """تحويل الصورة إلى Base64 لإرسالها لمحرك الرؤية (Vision AI)"""
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

# ------------------------------------------------------------------------------
# 5. محرك الذكاء الاصطناعي (Groq AI Engine & Prompts)
# ------------------------------------------------------------------------------

# تعليمات المعالجة التعليمية المكثفة
# تم تحديث هذا الجزء لضمان الشرح الطويل والمفصل
FILE_PROCESSING_PROMPT = """
أنت EduVise 🌟، خبير تعليمي محترف وموسوعي. مهمتك تحليل المحتوى المرفق (PDF، صورة، صوت) بدقة متناهية.
يجب أن يكون ردك "كاملاً جداً ومفصلاً" ولا تختصر أي معلومة مهما كانت.

يجب أن تتبع الهيكل التالي بدقة:

1. 📌 عنوان الدرس: عنوان جذاب وشامل للمحتوى.

2. 📖 الشرح التفصيلي العميق: 
   - اشرح كل مفهوم ورد في المحتوى بالتفصيل الممل.
   - استخدم لغة عربية فصيحة وسهلة.
   - توسع في شرح العلاقات بين الأفكار.
   - أضف معلومات إضافية من عندك لإثراء الشرح.
   - استخدم الإيموجيات لتبسيط المعلومة.

3. 💡 ملخص الأفكار الجوهرية: 
   - قائمة شاملة لكل نقطة تم ذكرها.

4. ✏️ أمثلة توضيحية وتطبيقية: 
   - قدم أمثلة واقعية تشرح كيفية تطبيق هذه المعلومات.

5. 📝 بنك الأسئلة الشامل: 
   - أسئلة مقالية (تحتاج تفكير).
   - أسئلة اختيار من متعدد (MCQ).
   - أسئلة صح وخطأ مع التعليل.

6. ✅ الأجوبة النموذجية: 
   - حلول مفصلة لكل سؤال مع شرح "لماذا" هذه هي الإجابة.

⚠️ ملاحظة هامة: إذا كان الملف كبيراً، لا تتوقف حتى تنهي كل شيء. أريد مقالاً تعليمياً ضخماً.
"""

CHAT_PROMPT = """
أنت EduVise 🌟، مساعد ذكي، ودود، واحترافي جداً. 
- رد بأسلوب لبق ومحفز للتعلم.
- استخدم الكثير من الإيموجيات التعليمية المناسبة.
- إذا سألك المستخدم عن معلومة، توسع في شرحها ولا تكتفِ بجملة واحدة.
- اجعل الطالب يشعر أنك مدرس خصوصي يهتم بكل تفاصيله.
"""

def get_ai_response(content, mode="text", history=None, media_path=None):
    """التواصل مع محرك Groq للحصول على الإجابة"""
    if not client: 
        return "⚠️ عذراً، محرك الذكاء الاصطناعي غير متوفر حالياً. تواصل مع الإدارة."
        
    try:
        # تحديد عدد التوكنز بناءً على رغبة المستخدم في الإطالة
        # الموديل 70b يدعم حتى 8192 توكن في الرد
        max_response_tokens = 8000 if mode != "vision" else 4000
        
        # الحالة الأولى: تحليل الصور (Vision Mode)
        if mode == "vision" and media_path:
            base64_image = encode_image(media_path)
            response = client.chat.completions.create(
                model="llama-3.2-11b-vision-preview",
                messages=[{
                    "role": "user", 
                    "content": [
                        {"type": "text", "text": FILE_PROCESSING_PROMPT},
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                    ]
                }],
                temperature=0.6, 
                max_tokens=4000
            )
            return response.choices[0].message.content
            
        # الحالة الثانية: تحليل الصوت (Audio Mode) عبر Whisper
        elif mode == "audio" and media_path:
            with open(media_path, "rb") as audio_file:
                transcription = client.audio.transcriptions.create(
                    file=(media_path, audio_file.read()),
                    model="whisper-large-v3",
                    response_format="text"
                )
            # بعد التفريغ الصوتي، نرسل النص للتحليل التعليمي
            return get_ai_response(
                f"حلل هذا المحتوى الصوتي بشكل كامل وشامل جداً:\n{transcription}", 
                mode="study_text"
            )
            
        # الحالة الثالثة: معالجة النصوص والملفات
        else:
            system_p = CHAT_PROMPT if mode == "text" else FILE_PROCESSING_PROMPT
            
            messages = [{"role": "system", "content": system_p}]
            
            # إضافة سياق المحادثة (History) ليتذكر البوت ما قيل سابقاً
            if history: 
                messages.extend(history)
                
            messages.append({"role": "user", "content": content})
            
            response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages, 
                temperature=0.7, 
                max_tokens=max_response_tokens
            )
            return response.choices[0].message.content
            
    except Exception as e:
        logger.error(f"❌ خطأ في محرك الذكاء الاصطناعي: {e}")
        return f"❌ حدث خطأ تقني أثناء معالجة طلبك. يرجى المحاولة لاحقاً.\nوصف الخطأ: {str(e)}"

# ------------------------------------------------------------------------------
# 6. أوامر البوت (Bot Commands)
# ------------------------------------------------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر البدء /start - تم تحسينه لضمان الاستجابة الفورية"""
    user_id = update.effective_user.id
    
    # محاولة التسجيل في الخلفية لضمان عدم تأخير رسالة الترحيب
    try:
        await register_user(update)
        log_message(user_id, "/start", 'command')
    except Exception as e:
        logger.error(f"⚠️ فشل التسجيل الأولي لـ {user_id}: {e}")

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
    
    await update.message.reply_text(
        welcome_text, 
        parse_mode='Markdown'
    )

async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر خاص بالمدير لعرض إحصائيات المستخدمين"""
    if update.effective_user.id != ADMIN_ID: 
        return
        
    try:
        with engine.connect() as connection:
            total = connection.execute(text('SELECT COUNT(*) FROM users')).scalar()
            active = connection.execute(text('SELECT COUNT(*) FROM users WHERE is_active = 1')).scalar()
            last_users = connection.execute(text("""
                SELECT user_id, first_name, username 
                FROM users ORDER BY join_date DESC LIMIT 30
            """)).fetchall()

        msg = f"📊 **إحصائيات البوت:**\n- الإجمالي: {total}\n- النشطون: {active}\n\n"
        msg += "📋 **آخر 30 عضو:**\n"
        
        for uid, name, uname in last_users:
            mention = f"@{uname}" if uname else "بدون يوزر"
            msg += f"👤 {name} | {mention} | `{uid}`\n"

        for part in split_text(msg):
            await update.message.reply_text(part, parse_mode='Markdown')
            
    except Exception as e:
        await update.message.reply_text(f"❌ فشل جلب البيانات: {e}")

async def get_message_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر للمدير لمراقبة آخر التفاعلات"""
    if update.effective_user.id != ADMIN_ID: 
        return
        
    try:
        with engine.connect() as connection:
            logs = connection.execute(text("""
                SELECT m.timestamp, m.message_content, m.message_type, u.first_name
                FROM messages m JOIN users u ON m.user_id = u.user_id
                ORDER BY m.timestamp DESC LIMIT 20; 
            """)).fetchall()

        msg = "📜 **آخر التفاعلات في البوت:**\n\n"
        for ts, content, mtype, name in logs:
            time_str = ts.strftime('%H:%M')
            preview = (content[:50] + '..') if len(content) > 50 else content
            msg += f"🕒 {time_str} | **{name}**\nنوع: {mtype}\nمحتوى: {preview}\n"
            msg += "-------------------\n"

        await update.message.reply_text(msg, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ فشل جلب السجلات: {e}")

async def clean_logs_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """أمر للمدير لتفريغ سجل الرسائل"""
    if update.effective_user.id != ADMIN_ID: 
        return
        
    try:
        with engine.connect() as connection:
            connection.execute(text("DELETE FROM messages"))
            connection.commit()
            await update.message.reply_text("✅ تم مسح سجل الرسائل بنجاح.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")

# ------------------------------------------------------------------------------
# 7. نظام البث للمستخدمين (Broadcast System)
# ------------------------------------------------------------------------------

BROADCAST_STATE = 1

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """بدء عملية البث"""
    if update.effective_user.id != ADMIN_ID: 
        return
    await update.message.reply_text("🎙️ أرسل الآن الرسالة التي تريد بثها لكل المستخدمين (نص، صورة، إلخ) أو أرسل /cancel.")
    return BROADCAST_STATE

async def broadcast_execute(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تنفيذ عملية البث لكل المستخدمين النشطين"""
    with engine.connect() as conn:
        users = [r[0] for r in conn.execute(text("SELECT user_id FROM users WHERE is_active = 1")).fetchall()]
        
    status_msg = await update.message.reply_text(f"⏳ جاري بدء البث إلى {len(users)} مستخدم...")
    
    success, fail = 0, 0
    for uid in users:
        try:
            # نسخ الرسالة كما هي (Copy Message)
            await context.bot.copy_message(
                chat_id=uid, 
                from_chat_id=update.effective_chat.id, 
                message_id=update.message.message_id
            )
            success += 1
            # تأخير بسيط لتجنب الـ Flood Limit من تيليجرام
            time.sleep(0.05)
        except Exception:
            fail += 1
            # إذا فشل الإرسال (حظر)، نقوم بتعطيل المستخدم
            update_user_status(uid, 0)
            
    await status_msg.edit_text(f"✅ اكتملت عملية البث:\n\n- نجاح: {success}\n- فشل (حظر/أخرى): {fail}")
    return ConversationHandler.END

async def broadcast_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء البث"""
    await update.message.reply_text("❌ تم إلغاء عملية البث.")
    return ConversationHandler.END

# ------------------------------------------------------------------------------
# 8. معالج الوسائط الموحد (Unified Media Handler)
# ------------------------------------------------------------------------------

async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """المسؤول عن معالجة الصور، الفيديو، الصوت، والـ PDF"""
    await register_user(update)
    msg = update.message
    user_id = update.effective_user.id
    
    # إظهار حالة "جاري الكتابة" أو "رفع الملف"
    status = await msg.reply_text("⏳ جاري استلام الملف ومعالجته...")
    temp_path = None
    
    try:
        # تحديد نوع الملف وإعداده للمعالجة
        if msg.photo:
            file_obj = await msg.photo[-1].get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.jpg"
            mode = "vision"
            await status.edit_text("👁️ جاري فحص الصورة وتحليل محتواها التعليمي...")
            
        elif msg.video:
            file_obj = await msg.video.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.mp4"
            mode = "audio"
            await status.edit_text("🎥 جاري استخراج الصوت من الفيديو وتحليله...")
            
        elif msg.voice or msg.audio:
            file_obj = await (msg.voice or msg.audio).get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.ogg"
            mode = "audio"
            await status.edit_text("🎧 جاري الاستماع للمقطع الصوتي وتحويله لشرح...")
            
        elif msg.document and msg.document.mime_type == "application/pdf":
            file_obj = await msg.document.get_file()
            temp_path = f"temp_{file_obj.file_unique_id}.pdf"
            mode = "pdf"
            await status.edit_text("📖 جاري قراءة صفحات PDF واستخراج المعلومات...")
            
        else:
            await status.edit_text("❌ عذراً، هذا النوع من الملفات غير مدعوم حالياً.")
            return

        # تحميل الملف محلياً
        await file_obj.download_to_drive(temp_path)
        log_message(user_id, f"File Upload: {mode}", mode)

        # تنفيذ عملية التحليل بناءً على النوع
        if mode == "pdf":
            doc = fitz.open(temp_path)
            # استخراج النص من أول 50 صفحة لضمان الشمولية وعدم تجاوز الحدود
            full_text = ""
            for page_num in range(min(len(doc), 50)):
                full_text += doc[page_num].get_text()
            doc.close()
            
            ai_reply = get_ai_response(
                f"إليك نص مستخرج من ملف PDF، قم بشرحه شرحاً وافياً ومطولاً جداً:\n\n{full_text[:35000]}", 
                mode="study_text"
            )
            
        elif mode == "vision":
            ai_reply = get_ai_response(None, mode="vision", media_path=temp_path)
            
        elif mode == "audio":
            ai_reply = get_ai_response(None, mode="audio", media_path=temp_path)

        # حذف رسالة الانتظار وإرسال الإجابة (المقسمة)
        await status.delete()
        for part in split_text(ai_reply):
            await msg.reply_text(part)
            
    except Exception as e:
        logger.error(f"❌ خطأ في معالج الوسائط: {e}")
        await status.edit_text(f"❌ حدث خطأ غير متوقع أثناء المعالجة: {str(e)}")
        
    finally:
        # حذف الملف المؤقت دائماً لتوفير المساحة
        if temp_path and os.path.exists(temp_path): 
            os.remove(temp_path)

# ------------------------------------------------------------------------------
# 9. معالج النصوص والدردشة (Text Handler)
# ------------------------------------------------------------------------------

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية والأسئلة المباشرة"""
    await register_user(update)
    user_id = update.effective_user.id
    user_input = update.message.text
    
    log_message(user_id, user_input, 'text')

    # الكلمات التي تجعل البوت يدخل في "وضع الشرح المكثف"
    study_keywords = ["اشرح", "شرح", "لخص", "حلل", "ما هو", "كيف", "سؤال", "درس", "موضوع"]
    is_study_mode = any(word in user_input.lower() for word in study_keywords)
    mode = "study_text" if is_study_mode else "text"

    # إدارة ذاكرة الجلسة (Context Memory) - آخر 10 رسائل
    history_key = f"hist_{user_id}"
    if history_key not in context.user_data:
        context.user_data[history_key] = []
    
    session_history = context.user_data[history_key]
    
    # إظهار علامة التفكير
    msg_wait = await update.message.reply_text("💡")
    
    try:
        # طلب الرد من الذكاء الاصطناعي
        ai_reply = get_ai_response(user_input, mode=mode, history=session_history)
        
        # تحديث السجل التاريخي للمحادثة
        session_history.append({"role": "user", "content": user_input})
        session_history.append({"role": "assistant", "content": ai_reply})
        context.user_data[history_key] = session_history[-10:] 

        await msg_wait.delete()
        
        # إرسال الإجابة المفصلة (مقسمة إذا كانت طويلة)
        for part in split_text(ai_reply):
            await update.message.reply_text(part)
            
    except Exception as e:
        logger.error(f"❌ خطأ في معالج النصوص: {e}")
        await msg_wait.edit_text(f"⚠️ واجهت مشكلة في معالجة طلبك: {e}")

# ------------------------------------------------------------------------------
# 10. تشغيل البوت (Main Runner)
# ------------------------------------------------------------------------------

def main():
    """نقطة انطلاق البوت"""
    if not BOT_TOKEN:
        print("❌ خطأ: BOT_TOKEN مفقود!")
        return
        
    # تهيئة قاعدة البيانات
    init_db()
    
    # إنشاء التطبيق
    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة الأوامر الأساسية
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command))
    app.add_handler(CommandHandler("messages_log", get_message_logs))
    app.add_handler(CommandHandler("clean_logs", clean_logs_command))

    # إضافة نظام البث (Conversation Handler)
    broadcast_conv = ConversationHandler(
        entry_points=[CommandHandler('broadcast', broadcast_start)],
        states={
            BROADCAST_STATE: [MessageHandler(filters.ALL & ~filters.COMMAND, broadcast_execute)]
        },
        fallbacks=[CommandHandler('cancel', broadcast_cancel)]
    )
    app.add_handler(broadcast_conv)

    # إضافة معالجات الوسائط (صور، صوت، ملفات)
    media_filters = (
        filters.PHOTO | 
        filters.VIDEO | 
        filters.AUDIO | 
        filters.VOICE | 
        filters.Document.PDF
    )
    app.add_handler(MessageHandler(media_filters, media_handler))

    # إضافة معالج النصوص
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    # تشغيل البوت (Webhook أو Polling)
    if WEBHOOK_URL:
        PORT = int(os.environ.get("PORT", 8443))
        app.run_webhook(
            listen="0.0.0.0", 
            port=PORT, 
            url_path=BOT_TOKEN, 
            webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
        )
        logger.info(f"🚀 البوت يعمل عبر Webhook على المنفذ {PORT}")
    else:
        logger.info("🚀 البوت يعمل عبر Polling...")
        app.run_polling()

if __name__ == "__main__":
    main()
