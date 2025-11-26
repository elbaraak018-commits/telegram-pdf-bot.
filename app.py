import os
import logging
import mimetypes # 🎯 مكتبة تخمين نوع الملف
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

# -----------------
# 1. إعداد السجلات والمتغيرات
# -----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# يجب تعيين هذه المتغيرات في بيئة التشغيل (مثل Render)
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")

if not BOT_TOKEN or not GEMINI_API_KEY or not WEBHOOK_URL:
    logging.warning("تحذير: تأكد من إعداد BOT_TOKEN, GEMINI_API_KEY, WEBHOOK_URL في بيئة التشغيل.")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# نص التعليمات الشاملة الموجهة لنموذج Gemini
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
    """الاستجابة لأمر /start."""
    greeting = "📘 مرحباً بك! أرسل لي أي ملف أو نص وسأجهزه كحزمة دراسية شاملة 🚀"
    await update.message.reply_text(greeting)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملفات (صورة، وثيقة، فيديو، صوت) وتحليلها باستخدام Gemini."""
    if not client:
        await update.message.reply_text("البوت غير مفعل. تأكد من المفاتيح.")
        return

    status_msg = await update.message.reply_text("جاري استلام الملف وتحليله... ⏳")
    
    # تحديد نوع الملف المرسل (قد يكون وثيقة، صورة، فيديو، أو صوت)
    file_obj = update.message.document or (update.message.photo[-1] if update.message.photo else None) or update.message.video or update.message.audio
    
    if not file_obj:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم.")
        return

    # الحصول على اسم الملف لتخمين الامتداد (مهم لملفات الوثائق)
    filename = file_obj.file_name if update.message.document else None
    
    # إنشاء مسار مؤقت للملف يتضمن اسم الملف أو معرِّفه الفريد
    file_name_part = filename if filename else file_obj.file_unique_id
    file_path = f"/tmp/{file_name_part}"
    
    try:
        # تنزيل الملف محلياً
        new_file = await context.bot.get_file(file_obj.file_id)
        os.makedirs('/tmp', exist_ok=True)
        await new_file.download_to_drive(file_path)

        # ⭐️ الخطوة الحاسمة: تخمين نوع MIME بناءً على الامتداد
        mime_type, _ = mimetypes.guess_type(file_path)
        
        # محاولات احتياطية لتخمين النوع إذا فشلت الطريقة القياسية
        if not mime_type:
            if update.message.photo:
                mime_type = 'image/jpeg' # افتراض نوع الصورة الأكثر شيوعاً
            elif update.message.video:
                mime_type = 'video/mp4' # افتراض نوع الفيديو
            elif update.message.audio:
                mime_type = 'audio/mp3' # افتراض نوع الصوت
            
        if not mime_type:
            await status_msg.edit_text("عذراً، لم أتمكن من تحديد نوع الملف (MIME Type) المطلوب لعملية التحليل.")
            return
            
        logging.info(f"تم تخمين نوع الملف: {mime_type}")

        # رفع الملف إلى Gemini مع تحديد mime_type لحل المشكلة
        uploaded_file = client.files.upload(
            file=file_path,
            mime_type=mime_type
        )
        
        # استدعاء نموذج Gemini للتحليل
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, uploaded_file]
        )

        await status_msg.edit_text("✅ تم التحليل!")
        await update.message.reply_text(response.text)

        # تنظيف الملفات من Gemini
        client.files.delete(name=uploaded_file.name)

    except Exception as e:
        # طباعة الخطأ في السجل لأغراض التصحيح
        logging.error(f"Error processing file: {e}") 
        await status_msg.edit_text(f"حدث خطأ أثناء المعالجة: {str(e)}")
    finally:
        # تنظيف الملف المحلي المؤقت
        if os.path.exists(file_path):
            os.remove(file_path)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الرسائل النصية فقط."""
    if not client:
        return

    msg = await update.message.reply_text("🤔 جاري التحليل...")
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[COMPREHENSIVE_PROMPT, update.message.text] # إرسال النص مع التعليمات
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
        logging.error("Bot Token مفقود!")
        return

    PORT = int(os.environ.get("PORT", 8443))
    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة معالجات الأوامر والرسائل
    app.add_handler(CommandHandler("start", start_command))
    
    # معالج الملفات: الصور، الوثائق (بما في ذلك PDF)، الفيديو، والصوت.
    file_filters = filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO
    app.add_handler(MessageHandler(file_filters, handle_document))
    
    # معالج النصوص: أي رسالة نصية ليست أمراً
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    print(f"Bot is running via Webhook on port {PORT}...")
    
    # تشغيل البوت باستخدام Webhook المناسب لخدمات مثل Render
    app.run_webhook(
        listen="0.0.0.0",
        port=PORT,
        url_path=BOT_TOKEN,
        webhook_url=f"{WEBHOOK_URL}/{BOT_TOKEN}"
    )

if __name__ == "__main__":
    main()
