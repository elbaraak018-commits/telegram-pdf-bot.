import os
import logging
import mimetypes
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import time 

# -----------------
# 1. إعداد السجلات والثوابت
# -----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096 
MAX_WAIT_TIME = 180 

# -----------------
# 2. وظيفة تقسيم النص 
# -----------------
def split_text(text, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    """تقسيم النص الطويل إلى أجزاء أصغر لتجنب حدود تيليجرام."""
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

# -----------------
# 3. المتغيرات البيئية والإعداد الأولي
# -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# 🔑 التعديل 1: تحديث البرومبت لطلب استخدام الإيموجيات المناسبة
COMPREHENSIVE_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية واسمك البراء. مهمتك تحليل أي محتوى تعليمي (نص، صوت، صور، فيديو، PDF، PPTX) وتحويله لحزمة دراسية شاملة ومزينة برموز إيموجي مناسبة لكل نقطة لتسهيل القراءة وجعل المظهر جذاباً.

**مهمتك المحددة:**
1.  ابدأ الرد بـ **عنوان الدرس** المناسب للمحتوى، مع إيموجي جذاب.
2.  قدم **الشرح المفصل والملخص** للمحتوى، واستخدم إيموجي 📚 أو 💡 لتنسيق النقاط الرئيسية.
3.  قدم **أمثلة تطبيقية**، واستخدم إيموجي ✏️ أو 🧪.
4.  قدم **مجموعة أسئلة متنوعة** (صح/خطأ، اختيار من متعدد، أكمل، علل)، واستخدم إيموجي ❓ أو 📝.
5.  قدم **الأجوبة النموذجية**، واستخدم إيموجي ✅ أو 💯.

ملاحظة هامة: لا تضف أي مقدمات أو شرح لمهامك أو أي عبارات تشير إلى تقسيم الردود. ابدأ مباشرة بعنوان الدرس والشرح.
"""

# -----------------
# 4. وظائف البوت
# -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب الجديدة"""
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

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملفات (صور، فيديو، صوت، وثائق)"""
    if not client:
        await update.message.reply_text("البوت غير مفعل. تأكد من المفاتيح.")
        return

    # 🔑 رسالة جاري المعالجة
    status_msg = await update.message.reply_text("⏳ جاري التحميل...") 
    
    file_obj = update.message.document or (update.message.photo[-1] if update.message.photo else None) or update.message.video or update.message.audio or update.message.voice 
    if not file_obj:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم. 🚫")
        return

    # تحديد مسار آمن
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
        await new_file.download_to_drive(file_path)

        # 2. تخمين نوع الملف
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            if update.message.photo: mime_type = 'image/jpeg'
            elif update.message.video: mime_type = 'video/mp4'
            
            elif update.message.audio or update.message.voice:
                if extension in ['.ogg', '.oga', '.opus']:
                    mime_type = 'audio/ogg' 
                elif extension in ['.mp3', '.mpeg']:
                    mime_type = 'audio/mpeg'
                elif extension in ['.wav']:
                    mime_type = 'audio/wav'
                else:
                    mime_type = 'audio/mpeg' 
            else: mime_type = 'application/pdf'

        logging.info(f"Processing file: {file_path} with type: {mime_type}")

        # 3. رفع الملف إلى Gemini
        uploaded_file = client.files.upload(
            file=file_path,
            config={'mime_type': mime_type}
        )
        uploaded_file_name = uploaded_file.name 

        # 4. انتظار جاهزية الملف 
        start_time = time.time()
        file_ready = False
        
        while time.time() - start_time < MAX_WAIT_TIME:
            elapsed_time = int(time.time() - start_time)
            # 🔑 رسالة جاري المعالجة أثناء الانتظار
            if elapsed_time % 10 == 0:
                await status_msg.edit_text("⏳ جاري معالجة الملف...") 
                
            file_status = client.files.get(name=uploaded_file_name)
            
            if file_status.state == 'ACTIVE':
                file_ready = True
                break
            
            time.sleep(5) 
        
        if not file_ready:
            raise TimeoutError("فشل في معالجة الملف على خوادم Google ضمن المهلة الزمنية المحددة. 😔")

        # 5. توليد المحتوى
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, uploaded_file]
        )

        # 6. تقسيم وإرسال الردود
        response_parts = split_text(response.text)
        
        await status_msg.edit_text("✅ تم التحليل بنجاح! جاري إرسال حزمتك الدراسية... 📦")

        for i, part in enumerate(response_parts):
            # الاحتفاظ بالعد وحذف الشرطة
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
    """معالجة النصوص العامة واستجابات الأسئلة المحددة (مثل الاسم)"""
    
    user_text = update.message.text.lower().strip()

    # 🔑 رسالة الإسم
    if "ما اسمك" in user_text or "من انت" in user_text:
        await update.message.reply_text("اسمي **البراء** 👋، وأنا بوتك المعلم والمساعد الدراسي الذكي، جاهز لخدمتك! 🧑‍🏫")
        return
    
    if not client: return

    # 🔑 رسالة جاري التحليل
    msg = await update.message.reply_text("🤔 جاري تحليل النص...") 
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, update.message.text]
        )
        response_parts = split_text(response.text)
        await msg.delete() 
        for i, part in enumerate(response_parts):
            # الاحتفاظ بالعد وحذف الشرطة
            prefix = f"الجزء {i+1}/{len(response_parts)}\n" if len(response_parts) > 1 else ""
            await update.message.reply_text(prefix + part)

    except Exception as e:
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"Text error: {error_message}")
        await msg.edit_text(f"خطأ: حدث خطأ أثناء معالجة النص. حاول مجدداً. 😞")


# -----------------
# 5. التشغيل (Webhook)
# -----------------
def main():
    if not BOT_TOKEN:
        print("Bot Token مفقود!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
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
