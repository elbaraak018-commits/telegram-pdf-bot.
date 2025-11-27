import os
import logging
import mimetypes
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

# -----------------
# 1. إعداد السجلات والثوابت
# -----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096 # الحد الأقصى لتيليجرام

# -----------------
# 2. وظيفة تقسيم النص (الحل لخطأ "Text is too long")
# -----------------
def split_text(text, max_len=MAX_TELEGRAM_MESSAGE_LENGTH):
    """تقسيم النص الطويل إلى أجزاء أصغر لتجنب حدود تيليجرام."""
    if len(text) <= max_len:
        return [text]

    parts = []
    current_part = ""
    
    # محاولة التقسيم عند فواصل الأسطر لتجنب قطع الكلمات
    for line in text.splitlines(keepends=True):
        if len(current_part) + len(line) <= max_len:
            # إضافة سطر جديد دون تجاوز الحد
            current_part += line
        else:
            # عندما يتجاوز الحد، نرسل الجزء الحالي ونبدأ جزءاً جديداً
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

COMPREHENSIVE_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية. مهمتك تحليل أي محتوى تعليمي (نص، صوت، صور، فيديو، PDF، PPTX) وتحويله لحزمة دراسية شاملة:
1. الشرح المفصل والملخص
2. أمثلة تطبيقية
3. مجموعة أسئلة متنوعة (صح/خطأ، اختيار من متعدد، أكمل، علل)
4. الأجوبة النموذجية
"""

# -----------------
# 4. وظائف البوت
# -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب"""
    await update.message.reply_text("📘 مرحباً بك! أرسل لي أي ملف أو نص وسأجهزه كحزمة دراسية شاملة 🚀")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملفات"""
    if not client:
        await update.message.reply_text("البوت غير مفعل. تأكد من المفاتيح.")
        return

    status_msg = await update.message.reply_text("جاري استلام الملف وتحليله... ⏳")
    
    file_obj = update.message.document or (update.message.photo[-1] if update.message.photo else None) or update.message.video or update.message.audio
    if not file_obj:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم.")
        return

    # تحديد مسار آمن (الحل ضد خطأ ascii)
    file_id = file_obj.file_unique_id
    extension = ''
    if update.message.document and file_obj.file_name:
        _, ext = os.path.splitext(file_obj.file_name)
        extension = ext
        
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
            # ... باقي التخمينات ...
            else: mime_type = 'application/pdf'

        logging.info(f"Processing file: {file_path} with type: {mime_type}")

        # 3. رفع الملف إلى Gemini (الحل ضد خطأ mime_type)
        uploaded_file = client.files.upload(
            file=file_path,
            config={'mime_type': mime_type}
        )
        uploaded_file_name = uploaded_file.name 

        # 4. توليد المحتوى
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, uploaded_file]
        )

        # 5. تقسيم وإرسال الردود (الحل ضد خطأ "Text is too long")
        response_parts = split_text(response.text)
        
        await status_msg.edit_text("✅ تم التحليل! جاري إرسال الحزمة الدراسية...")

        for i, part in enumerate(response_parts):
            prefix = f"--- الجزء {i+1}/{len(response_parts)} ---\n" if len(response_parts) > 1 else ""
            await update.message.reply_text(prefix + part)
        
        # التنظيف في النهاية
        # ... (التنظيف في finally) ...

    except Exception as e:
        # تأمين تسجيل الخطأ
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"FATAL ERROR IN DOCUMENT HANDLER: {error_message}")
            
        await status_msg.edit_text(f"❌ حدث خطأ داخلي أثناء المعالجة. حاول مجدداً.")
    
    finally:
        # حذف الملف المحلي
        if os.path.exists(file_path):
            os.remove(file_path)
        # محاولة تنظيف ملف Gemini
        if uploaded_file_name:
           try:
               client.files.delete(name=uploaded_file_name)
           except Exception as cleanup_e:
               logging.warning(f"Failed to clean up Gemini file: {cleanup_e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص"""
    if not client: return

    msg = await update.message.reply_text("🤔 جاري التحليل...")
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, update.message.text]
        )
        # تقسيم وإرسال الردود النصية أيضاً
        response_parts = split_text(response.text)
        await msg.delete() 
        for part in response_parts:
            await update.message.reply_text(part)

    except Exception as e:
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"Text error: {error_message}")
        await msg.edit_text(f"خطأ: حدث خطأ أثناء معالجة النص.")

# -----------------
# 5. التشغيل (Webhook)
# -----------------
def main():
    if not BOT_TOKEN:
        print("Bot Token مفقود!")
        return

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO, handle_document))
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
