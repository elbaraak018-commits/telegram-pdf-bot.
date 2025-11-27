import os
import logging
import mimetypes
import time 
# استيراد المكتبات اللازمة للاتصال بقاعدة بيانات Postgres
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler
from google import genai 

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

# جلب رابط الاتصال من المتغير البيئي (DATABASE_URL) الذي أنشأته على Render
DATABASE_URL = os.getenv("DATABASE_URL")
engine = None

if DATABASE_URL:
    try:
        # إنشاء محرك الاتصال
        engine = create_engine(DATABASE_URL)
        logging.info("PostgreSQL engine initialized successfully.")
    except Exception as e:
        logging.error(f"Failed to create PostgreSQL engine: {e}")
else:
    logging.warning("DATABASE_URL not set. Database functions will not work.")

def init_db():
    """تهيئة قاعدة البيانات وإنشاء جدول المستخدمين في Postgres."""
    if not engine:
        logging.error("Database engine is not available.")
        return

    try:
        with engine.connect() as connection:
            # إنشاء الجدول user_id كـ BIGINT (رقم كبير) لضمان استيعاب معرّفات المستخدمين في تيليجرام
            connection.execute(text(f"""
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY,
                    first_name TEXT,
                    username TEXT,
                    is_active INTEGER DEFAULT 1,
                    join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            connection.commit()
            logging.info("Users table created or confirmed in PostgreSQL.")
    except OperationalError as e:
        logging.error(f"PostgreSQL connection failed during init: {e}")
    except SQLAlchemyError as e:
        logging.error(f"SQLAlchemy error during init: {e}")


async def register_user(update: Update):
    """تسجيل المستخدم أو تحديث حالته في قاعدة بيانات Postgres."""
    if not engine: return
    user = update.effective_user
    
    # ضمان وجود الجدول قبل التسجيل
    init_db() 
    
    # استخدام INSERT INTO ON CONFLICT لضمان التحديث إذا كان موجوداً
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
    if len(text) <= max_len:
        return [text]
    
    parts = []
    current_part = ""
    for line in text.splitlines(keepends=True):
        if len(current_part) + len(line) <= max_len:
            current_part += line
        else:
            if current_part:
                parts.append(current_part.strip())
            current_part = line
    if current_part:
        parts.append(current_part.strip())
    return parts

# ----------------------------------------
# 4. إعدادات Gemini
# ----------------------------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

FILE_PROCESSING_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية واسمك البراء. مهمتك تحليل أي محتوى تعليمي (صورة، فيديو، ملف PDF، إلخ) وتحويله لحزمة دراسية شاملة ومزينة برموز إيموجي مناسبة لكل نقطة لتسهيل القراءة وجعل المظهر جذاباً.

**مهمتك المحددة:**
1.  ابدأ الرد بـ **عنوان الدرس** المناسب للمحتوى، مع إيموجي جذاب.
2.  قدم **الشرح المفصل والملخص** للمحتوى، واستخدم إيموجي 📚 أو 💡 لتنسيق النقاط الرئيسية.
3.  قدم **أمثلة تطبيقية**، واستخدم إيموجي ✏️ أو 🧪.
4.  قدم **مجموعة أسئلة متنوعة** (صح/خطأ، اختيار من متعدد، أكمل، علل)، واستخدم إيموجي ❓ أو 📝.
5.  قدم **الأجوبة النموذجية**، واستخدم إيموجي ✅ أو 💯.

ملاحظة هامة: لا تضف أي مقدمات أو شرح لمهامك أو أي عبارات تشير إلى تقسيم الردود. ابدأ مباشرة بعنوان الدرس والشرح.
"""

# ----------------------------------------
# 5. معالجات الأوامر (Handlers)
# ----------------------------------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await register_user(update) 
    
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

async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض عدد المستخدمين وقائمة بآخر 50 مستخدم (للمدير فقط)."""
    if not engine:
        await update.message.reply_text("🚫 خطأ: لا يوجد اتصال بقاعدة البيانات الخارجية.")
        return

    if update.effective_user.id != ADMIN_ID:
        return

    await register_user(update)

    try:
        with engine.connect() as connection:
            # 1. جلب العدد الإجمالي
            count_result = connection.execute(text('SELECT COUNT(user_id) FROM users WHERE is_active = 1')).fetchone()
            total_users = count_result[0] if count_result else 0
            
            # 2. جلب الأسماء ومعرف المستخدم (آخر 50)
            users_query = text('SELECT user_id, first_name, username FROM users WHERE is_active = 1 ORDER BY join_date DESC LIMIT 50')
            users_list = connection.execute(users_query).fetchall()
            
    except SQLAlchemyError as e:
        logging.error(f"Error fetching users from Postgres: {e}")
        await update.message.reply_text("❌ حدث خطأ في الاتصال بقاعدة البيانات.")
        return

    # بناء الرسالة كنص عادي لتجنب أخطاء التنسيق
    response = f"👥 إجمالي المستخدمين النشطين: {total_users}\n\n"
    response += "📋 آخر 50 اسم مسجل:\n"
    response += "-" * 20 + "\n"
    
    for user_id, first_name, username in users_list:
        name_display = first_name if first_name else "بدون اسم"
        user_link = f"@{username}" if username else "لا يوجد معرف"
        response += f"👤 {name_display} | {user_link}\n🆔 {user_id}\n\n"

    for part in split_text(response):
        await update.message.reply_text(part)

# ----------------------------------------
# 6. نظام البث
# ----------------------------------------
BROADCAST_START, BROADCAST_MESSAGE = range(2)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        return
        
    await update.message.reply_text("🎙️ **لوحة التحكم بالبث:**\n\nيرجى إرسال الرسالة التي تود بثها الآن. يمكنك إرسال صورة، فيديو، أو نص. (أرسل /cancel للإلغاء)", parse_mode='Markdown')
    return BROADCAST_MESSAGE

async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not engine:
        await update.message.reply_text("🚫 خطأ: لا يمكن إجراء البث لعدم توفر قاعدة البيانات.")
        return

    msg = await update.message.reply_text("⏳ جاري بدء عملية البث... هذا قد يستغرق بعض الوقت.")
    
    try:
        with engine.connect() as connection:
            cursor = connection.execute(text('SELECT user_id FROM users WHERE is_active = 1'))
            user_ids = [row[0] for row in cursor.fetchall()]
    except SQLAlchemyError as e:
        logging.error(f"Error fetching broadcast list: {e}")
        await msg.edit_text("❌ فشل جلب قائمة البث من قاعدة البيانات.")
        return

    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            await context.bot.forward_message(
                chat_id=user_id,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            success_count += 1
            time.sleep(0.05) 

        except Exception as e:
            fail_count += 1
            error_msg = str(e)
            if 'bot was blocked by the user' in error_msg or 'user is deactivated' in error_msg:
                 update_user_status(user_id, 0)
            
    await msg.edit_text(
        f"✅ عملية البث انتهت!\n\n"
        f"تم الإرسال بنجاح إلى: {success_count} مستخدم.\n"
        f"فشل الإرسال إلى: {fail_count} مستخدم."
    )
    return ConversationHandler.END

async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text('تم إلغاء عملية البث.')
    return ConversationHandler.END

# ----------------------------------------
# 7. معالج الملفات (المحسن)
# ----------------------------------------
async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # التسجيل التلقائي لأي شخص يرسل ملفاً
    await register_user(update) 

    if not client:
        await update.message.reply_text("البوت غير مفعل.")
        return

    status_msg = await update.message.reply_text("⏳") 
    
    file_obj = update.message.document or (update.message.photo[-1] if update.message.photo else None) or update.message.video or update.message.audio or update.message.voice 
    if not file_obj:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم. 🚫")
        return

    file_id = file_obj.file_unique_id
    extension = ''
    if update.message.document and file_obj.file_name:
        _, ext = os.path.splitext(file_obj.file_name)
        extension = ext
    elif update.message.audio or update.message.voice:
         extension = '.ogg' if update.message.voice else (os.path.splitext(file_obj.file_name)[1] if file_obj.file_name else '.mp3')
        
    file_path = f"/tmp/{file_id}{extension}" 
    uploaded_file_name = None 

    try:
        new_file = await context.bot.get_file(file_obj.file_id)
        os.makedirs('/tmp', exist_ok=True)
        
        if new_file.file_size > 50 * 1024 * 1024: 
             await status_msg.edit_text("❌ عذراً، حجم الملف يتجاوز 50 ميجابايت! 🚫")
             return

        await new_file.download_to_drive(file_path)

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            if update.message.photo: mime_type = 'image/jpeg'
            elif update.message.video: mime_type = 'video/mp4'
            elif update.message.audio or update.message.voice:
                if extension in ['.ogg', '.oga', '.opus']: mime_type = 'audio/ogg' 
                elif extension in ['.mp3', '.mpeg']: mime_type = 'audio/mpeg'
                elif extension in ['.wav']: mime_type = 'audio/wav'
                else: mime_type = 'audio/mpeg' 
            else: mime_type = 'application/pdf'

        logging.info(f"Processing file: {file_path} with type: {mime_type}")

        uploaded_file = client.files.upload(
            file=file_path,
            config={'mime_type': mime_type}
        )
        uploaded_file_name = uploaded_file.name 

        start_time = time.time()
        file_ready = False
        
        while time.time() - start_time < MAX_WAIT_TIME:
            elapsed_time = time.time() - start_time
            progress_percent = min(99, int((elapsed_time / MAX_WAIT_TIME) * 100))
            
            try:
                await status_msg.edit_text(f"⏳ {progress_percent}%") 
            except Exception:
                pass

            file_status = client.files.get(name=uploaded_file_name)
            
            if file_status.state == 'ACTIVE':
                file_ready = True
                break
            
            if file_status.state == 'FAILED':
                raise Exception(f"فشلت معالجة الملف.")

            time.sleep(5) 

        if not file_ready:
            raise TimeoutError(f"انتهت المهلة الزمنية.")

        await status_msg.edit_text(f"⏳ 100%") 

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[FILE_PROCESSING_PROMPT, uploaded_file]
        )

        response_parts = split_text(response.text)
        await status_msg.edit_text("✅ تم التحليل بنجاح! جاري إرسال حزمتك الدراسية... 📦")

        for i, part in enumerate(response_parts):
            prefix = f"الجزء {i+1}/{len(response_parts)}\n" if len(response_parts) > 1 else ""
            await update.message.reply_text(prefix + part)
        
    except Exception as e:
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"FATAL ERROR: {error_message}")
        await status_msg.edit_text(f"❌ حدث خطأ داخلي أثناء المعالجة.")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        if uploaded_file_name:
           try:
               client.files.delete(name=uploaded_file_name)
           except Exception:
               pass

# ----------------------------------------
# 8. معالج النصوص
# ----------------------------------------
async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # التسجيل التلقائي لأي شخص يرسل نصاً
    await register_user(update) 

    user_text = update.message.text.lower().strip()

    if "ما اسمك" in user_text or "من انت" in user_text:
        await update.message.reply_text("اسمي **البراء** 👋، وأنا بوتك المعلم والمساعد الدراسي الذكي.")
        return
    
    if not client: return

    msg = await update.message.reply_text("🤔") 
    
    try:
        SIMPLE_TEXT_PROMPT = f"""
        أنت مساعد ذكي واسمك البراء. أجب بشكل مختصر ومفيد ومناسب للسياق.
        النص: {update.message.text}
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[SIMPLE_TEXT_PROMPT]
        )
        response_parts = split_text(response.text)
        await msg.delete() 
        for i, part in enumerate(response_parts):
            prefix = f"الجزء {i+1}/{len(response_parts)}\n" if len(response_parts) > 1 else ""
            await update.message.reply_text(prefix + part)

    except Exception:
        await msg.edit_text(f"خطأ: حدث خطأ أثناء معالجة النص.")

# ----------------------------------------
# 9. التشغيل
# ----------------------------------------
def main():
    if not BOT_TOKEN:
        print("Bot Token مفقود!")
        return

    # 🔑 يتم استدعاء init_db عند بدء تشغيل البوت للتأكد من وجود جدول المستخدمين
    init_db() 
    
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command)) 
    
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler('broadcast', broadcast_command)],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_broadcast_message)]
        },
        fallbacks=[CommandHandler('cancel', cancel_broadcast)]
    )
    app.add_handler(broadcast_handler)

    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO | filters.VOICE, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text)) 

    PORT = int(os.environ.get("PORT", 8443))
    print(f"Bot is running via Webhook on port {PORT}...")
    
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
