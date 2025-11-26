import os
import logging
import mimetypes
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

# -----------------
# 1. إعداد السجلات
# -----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# -----------------
# 2. المتغيرات البيئية والإعداد الأولي
# -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN or not GEMINI_API_KEY or not WEBHOOK_URL:
    logging.warning("⚠️ تحذير: تأكد من إعداد BOT_TOKEN, GEMINI_API_KEY, WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# التعليمات للنظام
COMPREHENSIVE_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية. مهمتك تحليل أي محتوى تعليمي (نص، صوت، صور، فيديو، PDF، PPTX) وتحويله لحزمة دراسية شاملة:
1. الشرح المفصل والملخص
2. أمثلة تطبيقية
3. مجموعة أسئلة متنوعة (صح/خطأ، اختيار من متعدد، أكمل، علل)
4. الأجوبة النموذجية
"""

# -----------------
# 3. وظائف البوت
# -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """رسالة الترحيب"""
    await update.message.reply_text("📘 مرحباً بك! أرسل لي أي ملف أو نص وسأجهزه كحزمة دراسية شاملة 🚀")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملفات (صور، فيديو، صوت، وثائق)"""
    if not client:
        await update.message.reply_text("البوت غير مفعل. تأكد من المفاتيح.")
        return

    status_msg = await update.message.reply_text("جاري استلام الملف وتحليله... ⏳")
    
    file_obj = update.message.document or (update.message.photo[-1] if update.message.photo else None) or update.message.video or update.message.audio
    
    if not file_obj:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم.")
        return

    # 🔑 تحديد مسار آمن (الحل ضد خطأ ascii)
    file_id = file_obj.file_unique_id
    extension = ''
    if update.message.document and file_obj.file_name:
        _, ext = os.path.splitext(file_obj.file_name)
        extension = ext
        
    file_path = f"/tmp/{file_id}{extension}" 
    uploaded_file_name = None # متغير لتنظيف الملفات لاحقاً

    try:
        # تنزيل الملف
        new_file = await context.bot.get_file(file_obj.file_id)
        os.makedirs('/tmp', exist_ok=True)
        await new_file.download_to_drive(file_path)

        # تخمين نوع الملف (MIME Type)
        mime_type, _ = mimetypes.guess_type(file_path)
        
        # تخمين احتياطي
        if not mime_type:
            if update.message.photo: mime_type = 'image/jpeg'
            elif update.message.video: mime_type = 'video/mp4'
            elif update.message.audio: mime_type = 'audio/mp3'
            else: mime_type = 'application/pdf'

        logging.info(f"Processing file: {file_path} with type: {mime_type}")

        # رفع الملف إلى Gemini (الحل ضد خطأ mime_type)
        uploaded_file = client.files.upload(
            file=file_path,
            config={'mime_type': mime_type}
        )
        uploaded_file_name = uploaded_file.name # حفظ اسم الملف للتنظيف

        # توليد المحتوى
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, uploaded_file]
        )

        await status_msg.edit_text("✅ تم التحليل!")
        await update.message.reply_text(response.text)

        # ❌ (اختياري) قمنا بالتعليق على هذا السطر مؤقتاً لتجنب أخطاء الإذن بعد الرد!
        # client.files.delete(name=uploaded_file.name) 

    except Exception as e:
        # 🔑 تأمين تسجيل الخطأ (للتشخيص في سجلات Render)
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"FATAL ERROR IN DOCUMENT HANDLER: {error_message}")
            
        await status_msg.edit_text(f"❌ حدث خطأ داخلي أثناء المعالجة. حاول مجدداً.")
    
    # 5. تنظيف الملف المحلي بغض النظر عن النتيجة
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        # محاولة تنظيف ملف Gemini إذا تم رفعه بنجاح
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
        await msg.delete()
        await update.message.reply_text(response.text)
    except Exception as e:
        # تأمين تسجيل الخطأ
        error_message = str(e).encode('utf-8', errors='ignore').decode('utf-8')
        logging.error(f"Text error: {error_message}")
        await msg.edit_text(f"خطأ: حدث خطأ أثناء معالجة النص.")

# -----------------
# 4. التشغيل (Webhook)
# -----------------
def main():
    if not BOT_TOKEN:
        print("Bot Token مفقود!")
        return

    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة المعالجات لجميع أنواع الملفات والنصوص
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
