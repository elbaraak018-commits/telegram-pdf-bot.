import os
import logging
import mimetypes
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

# إعداد السجلات
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# -----------------
# 1. إعداد المتغيرات البيئية
# -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")  # الرابط HTTPS الخاص بخدمة Render

if not BOT_TOKEN or not GEMINI_API_KEY or not WEBHOOK_URL:
    print("تحذير: تأكد من إعداد BOT_TOKEN, GEMINI_API_KEY, WEBHOOK_URL")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# نص التعليمات الشاملة
COMPREHENSIVE_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية. مهمتك تحليل أي محتوى تعليمي (نص، صوت، صور، فيديو، PDF، PPTX) وتحويله لحزمة دراسية شاملة:
1. الشرح المفصل والملخص
2. أمثلة تطبيقية
3. مجموعة أسئلة متنوعة (صح/خطأ، اختيار من متعدد، أكمل، علل)
4. الأجوبة النموذجية
"""

# -----------------
# 2. وظائف البوت
# -----------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    greeting = "📘 مرحباً بك! أرسل لي أي ملف أو نص وسأجهزه كحزمة دراسية شاملة 🚀"
    await update.message.reply_text(greeting)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not client:
        await update.message.reply_text("البوت غير مفعل. تأكد من المفاتيح.")
        return

    status_msg = await update.message.reply_text("جاري استلام الملف وتحليله... ⏳")
    
    # تحديد الملف المرسل
    file_obj = update.message.document or (update.message.photo[-1] if update.message.photo else None) or update.message.video or update.message.audio
    if not file_obj:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم.")
        return

    file_path = f"/tmp/{file_obj.file_unique_id}"
    try:
        new_file = await context.bot.get_file(file_obj.file_id)
        os.makedirs('/tmp', exist_ok=True)
        await new_file.download_to_drive(file_path)

        # تحديد MIME Type تلقائياً
        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "application/octet-stream"  # fallback عام لأي نوع ملف

        # رفع الملف إلى Gemini
        uploaded_file = client.files.upload(file=file_path, mime_type=mime_type)
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, uploaded_file]
        )

        await status_msg.edit_text("✅ تم التحليل!")
        await update.message.reply_text(response.text)

        # تنظيف الملفات من Gemini
        client.files.delete(name=uploaded_file.name)

    except Exception as e:
        await status_msg.edit_text(f"حدث خطأ أثناء المعالجة: {str(e)}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not client:
        return

    msg = await update.message.reply_text("🤔 جاري التحليل...")
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[update.message.text]
        )
        await msg.delete()
        await update.message.reply_text(response.text)
    except Exception as e:
        await msg.edit_text(f"خطأ: {e}")

# -----------------
# 3. تشغيل Webhook
# -----------------
def main():
    if not BOT_TOKEN:
        print("Bot Token مفقود!")
        return

    PORT = int(os.environ.get("PORT", 8443))
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"Bot is running via Webhook on port {PORT}...")
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
