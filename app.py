# app.py - المعلم الذكي التفاعلي Multimodal مع دالة الخادم الوهمي
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
from google.genai import types

# -----------------
# 1. إعدادات البوت والـ Gemini
# -----------------
# يتم قراءة المفاتيح السرية من متغيرات البيئة في منصة Render
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

if not BOT_TOKEN or not GEMINI_API_KEY:
    # هذا الشرط سيتم تحقيقه إذا لم تضع المفاتيح في Render
    print("خطأ: مفاتيح البوت أو Gemini مفقودة في المتغيرات البيئية!")
    # يجب عدم إثارة الاستثناء لتمكين الخادم الوهمي من العمل
    # raise ValueError("Missing required environment variables (BOT_TOKEN or GEMINI_API_KEY)")

# قم بتهيئة عميل Gemini (سيتم تهيئته فقط إذا كان المفتاح موجوداً)
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
    """معالجة الملفات المرفقة (PDF، صور، صوتيات، الخ)"""
    if not client:
        await update.message.reply_text("عذراً، البوت غير مُفعّل. يُرجى التأكد من إدخال مفتاح Gemini API.")
        return
    
    await update.message.reply_text("بدأت عملية التحليل المعقدة للملف... قد يستغرق هذا بضع ثوانٍ.")
    
    # تحديد نوع الملف
    if update.message.document:
        file_info = update.message.document
    elif update.message.photo:
        file_info = update.message.photo[-1]
    elif update.message.video:
        file_info = update.message.video
    elif update.message.audio:
        file_info = update.message.audio
    else:
        await update.message.reply_text("عذراً، نوع الملف غير مدعوم حالياً.")
        return

    # تحميل الملف مؤقتاً
    file = await context.bot.get_file(file_info.file_id)
    file_name = file_info.file_name if hasattr(file_info, 'file_name') else f"file_{file_info.file_unique_id}.tmp"
    file_path = f"/tmp/{file_name}"
    
    os.makedirs('/tmp', exist_ok=True)
    await file.download_to_drive(file_path)

    uploaded_file = None
    try:
        uploaded_file = client.files.upload(file=file_path)
        contents = [COMPREHENSIVE_PROMPT, uploaded_file]
        
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=contents
        )
        
        await update.message.reply_text(response.text)
        
    except Exception as e:
        print(f"Gemini Error: {e}")
        await update.message.reply_text(f"عذراً، حدث خطأ أثناء تحليل الملف عبر Gemini: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)
        if uploaded_file:
            client.files.delete(name=uploaded_file.name)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الأسئلة النصية التفاعلية"""
    if not client:
        await update.message.reply_text("عذراً، البوت غير مُفعّل. يُرجى التأكد من إدخال مفتاح Gemini API.")
        return
        
    user_text = update.message.text
    
    await update.message.reply_text("جاري التفكير والرد على سؤالك...")

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[user_text]
        )
        
        await update.message.reply_text(response.text)
        
    except Exception as e:
        await update.message.reply_text(f"عذراً، حدث خطأ أثناء الإجابة على سؤالك: {e}")
    
# -----------------
# 3. تشغيل البوت والخادم الوهمي
# -----------------

def main():
    if not BOT_TOKEN:
        print("لا يمكن بدء البوت لأن التوكن مفقود.")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()

    # إضافة Handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("البوت يعمل باستخدام طريقة الاستطلاع (Polling)...")
    app.run_polling(poll_interval=3)


# هذه هي الدالة الإضافية التي يحتاجها Render لنجاح فحص الصحة
def dummy_app(environ, start_response):
    """
    هذه دالة وهمية لتشغيل خادم Waitress لنجاح فحص الصحة في Render.
    """
    start_response('200 OK', [('Content-Type', 'text/plain')])
    return [b"Bot is Running (Worker Process)"]


if __name__ == '__main__':
    main()
