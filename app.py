import os
import logging
import mimetypes
import time 
import sqlite3 # 🔑 جديد: مكتبة قاعدة البيانات
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes, ConversationHandler

# ----------------------------------------
# 1. الإعدادات والثوابت
# ----------------------------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096 
MAX_WAIT_TIME = 300 # 5 دقائق للملفات الكبيرة
ADMIN_ID = 1050772765 # 🔑 معرف المدير الخاص بك

# ----------------------------------------
# 2. إعدادات قاعدة البيانات (SQLite3)
# ----------------------------------------

def init_db():
    """تهيئة قاعدة البيانات وإنشاء جدول المستخدمين."""
    # سيتم إنشاء ملف 'users.db' في نفس مسار البوت
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            first_name TEXT,
            username TEXT,
            is_active INTEGER,
            join_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    conn.close()

async def register_user(update: Update):
    """تسجيل المستخدم أو تحديث حالته في قاعدة البيانات عند الضغط على /start."""
    user = update.effective_user
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    # 🔑 محاولة إدخال أو تحديث بيانات المستخدم (تحديث is_active إلى 1)
    cursor.execute('''
        INSERT OR REPLACE INTO users (user_id, first_name, username, is_active) 
        VALUES (?, ?, ?, 1)
    ''', (user.id, user.first_name, user.username or '', ))
    
    conn.commit()
    conn.close()

def update_user_status(user_id, status):
    """دالة مساعدة لتحديث حالة المستخدم (0 = غير نشط/حظر، 1 = نشط)."""
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE users SET is_active = ? WHERE user_id = ?', (status, user_id))
    conn.commit()
    conn.close()

# ----------------------------------------
# 3. الوظائف المساعدة
# ----------------------------------------

def split_text(text, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    """تقسيم النص الطويل."""
    if len(text) <= max_len:
        return [text]
    # (تم اختصار محتوى الدالة لتبسيط الكود، ولكنها تقوم بنفس عمل التقسيم السابق)
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
# 4. إعدادات Gemini والبرومبت
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

# 5.1. أمر /start
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يسجل المستخدم ويرسل رسالة الترحيب."""
    await register_user(update) # 🔑 تسجيل المستخدم
    
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

# 5.2. أمر /users (لعرض المستخدمين)
async def get_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """يعرض عدد المستخدمين وقائمة بأسمائهم (للمدير فقط)."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 هذا الأمر مخصص للمدير فقط.")
        return

    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    
    # 1. جلب العدد الإجمالي
    cursor.execute('SELECT COUNT(user_id) FROM users WHERE is_active = 1')
    total_users = cursor.fetchone()[0]
    
    # 2. جلب الأسماء (أول 50 اسم)
    cursor.execute('SELECT first_name, username FROM users WHERE is_active = 1 ORDER BY join_date DESC LIMIT 50')
    users_list = cursor.fetchall()
    
    conn.close()

    response = f"👥 **إجمالي المستخدمين النشطين: {total_users}**\n\n"
    response += "📋 **آخر 50 اسم مسجل:**\n"
    
    for first_name, username in users_list:
        if username:
            response += f"- {first_name} (@{username})\n"
        else:
            response += f"- {first_name}\n"

    for part in split_text(response):
        await update.message.reply_text(part, parse_mode='Markdown')

# ----------------------------------------
# 6. نظام البث (Conversation Handler)
# ----------------------------------------
BROADCAST_START, BROADCAST_MESSAGE = range(2)

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تبدأ عملية البث (للمدير فقط)."""
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("🚫 هذا الأمر مخصص للمدير فقط.")
        return
        
    await update.message.reply_text("🎙️ **لوحة التحكم بالبث:**\n\nيرجى إرسال الرسالة التي تود بثها الآن. يمكنك إرسال صورة، فيديو، أو نص. (أرسل /cancel للإلغاء)", parse_mode='Markdown')
    
    return BROADCAST_MESSAGE

async def receive_broadcast_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """تستقبل الرسالة وتبدأ عملية الإرسال المتتابع."""
    msg = await update.message.reply_text("⏳ جاري بدء عملية البث... هذا قد يستغرق بعض الوقت.", parse_mode='Markdown')
    
    conn = sqlite3.connect('users.db')
    cursor = conn.cursor()
    cursor.execute('SELECT user_id FROM users WHERE is_active = 1')
    user_ids = [row[0] for row in cursor.fetchall()]
    conn.close()

    success_count = 0
    fail_count = 0
    
    for user_id in user_ids:
        try:
            # 🔑 استخدام دالة forward_message للحفاظ على تنسيق الملفات (صورة، فيديو، الخ)
            await context.bot.forward_message(
                chat_id=user_id,
                from_chat_id=update.effective_chat.id,
                message_id=update.message.message_id
            )
            success_count += 1
            # 🔑 فترة تأخير بسيطة لمنع تخطي حدود تيليجرام
            time.sleep(0.05) 

        except Exception as e:
            fail_count += 1
            if 'bot was blocked by the user' in str(e) or 'user is deactivated' in str(e):
                 # تحديث الحالة إلى غير نشط لتجنب الإرسال مستقبلاً
                 update_user_status(user_id, 0)
            
    await msg.edit_text(
        f"✅ **عملية البث انتهت!**\n\n"
        f"تم الإرسال بنجاح إلى: {success_count} مستخدم.\n"
        f"فشل الإرسال إلى: {fail_count} مستخدم.", 
        parse_mode='Markdown'
    )
    
    return ConversationHandler.END

async def cancel_broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """إلغاء عملية البث."""
    await update.message.reply_text('تم إلغاء عملية البث.', parse_mode='Markdown')
    return ConversationHandler.END

# ----------------------------------------
# 7. معالجات الملفات والنصوص المعتادة
# ----------------------------------------

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملفات (صور، فيديو، صوت، وثائق)"""
    if not client:
        await update.message.reply_text("البوت غير مفعل. تأكد من المفاتيح.")
        return

    # رسالة جاري المعالجة
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
        # 1. تنزيل الملف
        new_file = await context.bot.get_file(file_obj.file_id)
        os.makedirs('/tmp', exist_ok=True)
        
        # التحقق من حد حجم الملف (50 ميجابايت)
        if new_file.file_size > 50 * 1024 * 1024: 
             await status_msg.edit_text("❌ عذراً، حجم الملف يتجاوز 50 ميجابايت، وهو الحد الأقصى! 🚫")
             return

        await new_file.download_to_drive(file_path)

        # 2. تخمين نوع الملف
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

        # 3. رفع الملف إلى Gemini
        uploaded_file = client.files.upload(
            file=file_path,
            config={'mime_type': mime_type}
        )
        uploaded_file_name = uploaded_file.name 

        # 4. انتظار جاهزية الملف (مع تحديث النسبة المئوية)
        start_time = time.time()
        file_ready = False
        
        while time.time() - start_time < MAX_WAIT_TIME:
            elapsed_time = time.time() - start_time
            
            progress_percent = min(100, int((elapsed_time / MAX_WAIT_TIME) * 100))
            
            try:
                await status_msg.edit_text(f"⏳ {progress_percent}%") 
            except Exception as edit_e:
                logging.warning(f"Error editing status message: {edit_e}")

            file_status = client.files.get(name=uploaded_file_name)
            
            if file_status.state == 'ACTIVE':
                file_ready = True
                break
            
            if file_status.state == 'FAILED':
                raise Exception(f"فشلت معالجة الملف على خوادم Gemini.")

            time.sleep(5) # الانتظار 5 ثواني

        if not file_ready:
            raise TimeoutError(f"فشل في معالجة الملف ضمن المهلة الزمنية المحددة ({MAX_WAIT_TIME} ثانية). 😔")

        # 5. توليد المحتوى
        await status_msg.edit_text(f"⏳ 100%") 

        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[FILE_PROCESSING_PROMPT, uploaded_file]
        )

        # 6. تقسيم وإرسال الردود
        response_parts = split_text(response.text)
        
        await status_msg.edit_text("✅ تم التحليل بنجاح! جاري إرسال حزمتك الدراسية... 📦")

        for i, part in enumerate(response_parts):
            prefix = f"الجزء {i+1}/{len(response_parts)}\n" if len(response_parts) > 1 else ""
            await update.message.reply_text(prefix + part)
        
    except Exception as e:
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"FATAL ERROR IN DOCUMENT HANDLER: {error_message}")
            
        await status_msg.edit_text(f"❌ حدث خطأ داخلي أثناء المعالجة. حاول مجدداً. تأكد من أن الملف ليس كبيراً جداً. 😟")
    
    finally:
        # 7. التنظيف النهائي
        if os.path.exists(file_path):
            os.remove(file_path)
        if uploaded_file_name:
           try:
               client.files.delete(name=uploaded_file_name)
           except Exception as cleanup_e:
               logging.warning(f"Failed to clean up Gemini file: {cleanup_e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص العامة والأسئلة (يجيب على النص فقط بـ إجابة مختصرة ومفيدة)"""
    
    user_text = update.message.text.lower().strip()

    if "ما اسمك" in user_text or "من انت" in user_text:
        await update.message.reply_text("اسمي **البراء** 👋، وأنا بوتك المعلم والمساعد الدراسي الذكي، جاهز لخدمتك! 🧑‍🏫")
        return
    
    if not client: return

    msg = await update.message.reply_text("🤔") 
    
    try:
        SIMPLE_TEXT_PROMPT = f"""
        أنت مساعد ذكي واسمك البراء. مهمتك الإجابة على استفسارات المستخدمين بشكل **مختصر، ومفيد، ومناسب للسياق**، مع استخدام الإيموجي المناسب. لا تقم بتوليد أسئلة أو أمثلة أو شروحات طويلة. أجب على السؤال أو حلل النص مباشرة. 
        النص/السؤال: {update.message.text}
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

    except Exception as e:
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"Text error: {error_message}")
        await msg.edit_text(f"خطأ: حدث خطأ أثناء معالجة النص. حاول مجدداً. 😞")


# ----------------------------------------
# 8. التشغيل (main)
# ----------------------------------------
def main():
    if not BOT_TOKEN:
        print("Bot Token مفقود!")
        return

    # 🔑 تهيئة قاعدة البيانات قبل بدء البوت
    init_db() 
    
    app = Application.builder().token(BOT_TOKEN).build()

    # معالجات الأوامر العادية
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("users", get_users_command)) 
    
    # معالج المحادثة لنظام البث
    broadcast_handler = ConversationHandler(
        entry_points=[CommandHandler('broadcast', broadcast_command)],
        states={
            BROADCAST_MESSAGE: [MessageHandler(filters.ALL & ~filters.COMMAND, receive_broadcast_message)]
        },
        fallbacks=[CommandHandler('cancel', cancel_broadcast)]
    )
    app.add_handler(broadcast_handler)

    # معالجات الملفات والنصوص المعتادة
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
