import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai

# إعداد السجلات (Logging)
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# -----------------
# 1. إعدادات البوت والـ Gemini
# -----------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    print("تحذير: مفاتيح البوت أو Gemini مفقودة في المتغيرات البيئية.")

# تهيئة عميل Gemini
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# التعليمات الشاملة (الـ Prompt)
COMPREHENSIVE_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية. مهمتك هي تحليل أي محتوى تعليمي مُرسل إليك (نصي، مرئي، سمعي) وتحويله إلى حزمة دراسية شاملة.

عندما يرسل لك المستخدم محتوى (ملف PDF، مقطع صوتي، صورة، عرض تقديمي، أو فيديو): يجب عليك تنفيذ التسلسل التالي كاملاً في رسالة واحدة:
1. الشرح المفصل والملخص (Summary): قدم شرحاً كاملاً ومفصلاً لجميع محاور المحتوى.
2. الأمثلة والتطبيق (Examples): قدم ثلاثة أمثلة تطبيقية مشروحة خطوة بخطوة.
3. حزمة التقييم الشاملة (Quiz and Exercises): قم بإنشاء مجموعة من (3 أسئلة صح/خطأ، 3 اختيار من متعدد، 3 أكمل الفراغ، 3 علل/اشرح بالتفصيل).
4. الأجوبة النموذجية (Answer Key): أدرج قسم نهائي يحتوي على الأجوبة لجميع الفقرات.
"""

# -----------------
# 2. وظائف البوت
# -----------------

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد على أمر /start"""
    greeting = "📘 مرحباً بك! أرسل لي ملف PDF، صورة، مقطع صوتي، أو اطرح أي سؤال نصي وسأقوم بتحليله وإعداد حزمة دراسية شاملة لك. 🚀"
    await update.message.reply_text(greeting)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملفات المرفقة"""
    if not client:
        await update.message.reply_text("عذراً، البوت غير مُفعّل. تأكد من المفاتيح.")
        return
    
    status_msg = await update.message.reply_text("جاري استلام الملف وتحليله... ⏳")
    
    # تحديد نوع الملف
    file_obj = None
    if update.message.document:
        file_obj = update.message.document
    elif update.message.photo:
        file_obj = update.message.photo[-1]
    elif update.message.video:
        file_obj = update.message.video
    elif update.message.audio:
        file_obj = update.message.audio
    else:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم.")
        return

    # مسار مؤقت
    file_path = f"/tmp/{file_obj.file_unique_id}"
    
    try:
        # تحميل الملف
        new_file = await context.bot.get_file(file_obj.file_id)
        os.makedirs('/tmp', exist_ok=True)
        await new_file.download_to_drive(file_path)

        # الرفع إلى Gemini
        uploaded_file = client.files.upload(file=file_path)
        
        # التوليد
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=[COMPREHENSIVE_PROMPT, uploaded_file]
        )
        
        await status_msg.edit_text("✅ تم التحليل بنجاح! إليك النتيجة:")
        await update.message.reply_text(response.text)
        
        # تنظيف Gemini
        client.files.delete(name=uploaded_file.name)

    except Exception as e:
        print(f"Error: {e}")
        await status_msg.edit_text(f"حدث خطأ أثناء المعالجة: {str(e)}")
    
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص"""
    if not client: return
    
    msg = await update.message.reply_text("🤔")
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
# 3. الدالة الوهمية (للخادم) والتشغيل
# -----------------

# هذه هي الدالة التي سيقوم Waitress بتشغيلها
def dummy_app(environ, start_response):
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b"Bot is Running via Polling"]

def main():
    if not BOT_TOKEN:
        print("Bot Token is missing!")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Bot is polling...")
    app.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
