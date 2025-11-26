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
    # طباعة تحذير في السجلات إذا كانت المفاتيح مفقودة
    print("تحذير: مفاتيح البوت أو Gemini مفقودة في المتغيرات البيئية.")

# تهيئة عميل Gemini
client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

# التعليمات الشاملة (الـ Prompt) لتوجيه نموذج Gemini
COMPREHENSIVE_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي ومحترف للغاية. مهمتك هي تحليل أي محتوى تعليمي مُرسل إليك (نصي، مرئي، سمعي، عرض تقديمي) وتحويله إلى حزمة دراسية شاملة. يجب أن يكون الشرح مفصلاً جداً وواضحاً.

عند تحليل المحتوى، يجب عليك تنفيذ التسلسل التالي كاملاً في رسالة واحدة:
1. الشرح المفصل والملخص (Comprehensive Summary): قدم شرحاً كاملاً ومفصلاً لجميع محاور المحتوى، مع التركيز على المفاهيم الأساسية.
2. الأمثلة والتطبيق (Detailed Examples): قدم ثلاثة أمثلة تطبيقية مشروحة خطوة بخطوة ومرتبطة بمجال المحتوى.
3. حزمة التقييم الشاملة (Quiz and Exercises): قم بإنشاء مجموعة من التمارين متنوعة على النحو التالي (3 أسئلة صح/خطأ، 3 اختيار من متعدد، 3 أكمل الفراغ، 3 علل/اشرح بالتفصيل).
4. الأجوبة النموذجية (Answer Key): أدرج قسماً نهائياً يحتوي على الأجوبة النموذجية لجميع الفقرات المذكورة أعلاه.
"""

# قائمة أنواع الملفات المدعومة بواسطة Gemini والتي يمكن أن تأتي من تيليجرام
SUPPORTED_MIME_TYPES = {
    'application/pdf', 
    'image/jpeg', 'image/png', 'image/webp',
    'video/mp4', 'video/quicktime', 'video/webm',
    'audio/mp3', 'audio/wav', 'audio/ogg',
    # دعم إضافي للمستندات
    'application/vnd.openxmlformats-officedocument.presentationml.presentation', # PPTX
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',  # DOCX
    'application/vnd.ms-powerpoint', # PPT القديم
    'application/vnd.ms-word', # DOC القديم
}


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """الرد على أمر /start"""
    greeting = "📘 مرحباً بك! أرسل لي ملف PDF، صورة، مقطع صوتي، فيديو، أو عرض تقديمي (PPTX) وسأقوم بتحليله وإعداد حزمة دراسية شاملة لك. 🚀"
    await update.message.reply_text(greeting)

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة الملفات المرفقة (PDF, PPTX, صور, فيديو, صوت)"""
    if not client:
        await update.message.reply_text("عذراً، البوت غير مُفعّل. تأكد من المفاتيح.")
        return
    
    status_msg = await update.message.reply_text("جاري استلام الملف والتحقق من نوعه... ⏳")
    
    file_obj = None
    mime_type = None
    file_name = None
    
    # 1. تحديد File Object و MIME Type
    if update.message.document:
        file_obj = update.message.document
        mime_type = file_obj.mime_type
        file_name = file_obj.file_name if file_obj.file_name else "document.tmp"
    elif update.message.photo:
        # الصور المرسلة كصور (وليست كمستند) يتم تحديد نوعها يدوياً لضمان الدقة
        file_obj = update.message.photo[-1] # اختيار أكبر جودة
        mime_type = 'image/jpeg' 
        file_name = f"photo_{file_obj.file_unique_id}.jpg"
    elif update.message.video:
        file_obj = update.message.video
        mime_type = file_obj.mime_type if file_obj.mime_type else 'video/mp4'
        file_name = f"video_{file_obj.file_unique_id}.mp4"
    elif update.message.audio:
        file_obj = update.message.audio
        mime_type = file_obj.mime_type if file_obj.mime_type else 'audio/mp3'
        file_name = f"audio_{file_obj.file_unique_id}.mp3"
    else:
        await status_msg.edit_text("عذراً، نوع الملف غير مدعوم.")
        return

    # 2. التحقق من أن نوع MIME مدعوم
    if not mime_type or mime_type not in SUPPORTED_MIME_TYPES:
        await status_msg.edit_text(f"عذراً، صيغة الملف غير مدعومة حالياً للتحليل العميق (نوع: {mime_type}). يرجى إرسال ملفات شائعة مثل PDF, PPTX, JPG, MP4, أو MP3.")
        return

    # مسار مؤقت
    file_path = f"/tmp/{file_name}"
    
    try:
        # 3. تحميل الملف
        new_file = await context.bot.get_file(file_obj.file_id)
        os.makedirs('/tmp', exist_ok=True)
        await new_file.download_to_drive(file_path)

        # 4. الرفع إلى Gemini مع تحديد نوع الملف الموثوق
        await status_msg.edit_text(f"جاري رفع وتحليل الملف ({mime_type})...")
        uploaded_file = client.files.upload(file=file_path, mime_type=mime_type)
        
        # 5. التوليد
        response = client.models.generate_content(
            model='gemini-2.5-flash', 
            contents=[COMPREHENSIVE_PROMPT, uploaded_file]
        )
        
        await status_msg.edit_text("✅ تم التحليل بنجاح! إليك النتيجة الشاملة:")
        await update.message.reply_text(response.text)
        
        # 6. تنظيف Gemini
        client.files.delete(name=uploaded_file.name)

    except Exception as e:
        print(f"Error: {e}")
        # عرض رسالة خطأ واضحة للمستخدم
        await status_msg.edit_text(f"عذراً، حدث خطأ أثناء المعالجة: {str(e)}")
    
    finally:
        # 7. تنظيف الملفات المؤقتة
        if os.path.exists(file_path):
            os.remove(file_path)

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """معالجة النصوص (للأسئلة العادية)"""
    if not client: return
    
    msg = await update.message.reply_text("🤔")
    try:
        # الرد الطبيعي على الأسئلة النصية
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[update.message.text]
        )
        await msg.delete()
        await update.message.reply_text(response.text)
    except Exception as e:
        await msg.edit_text(f"خطأ: {e}")

# -----------------
# 3. دالة الخادم الوهمية والتشغيل
# -----------------

# هذه الدالة ضرورية لنجاح فحص Render (Health Check)
def dummy_app(environ, start_response):
    status = '200 OK'
    headers = [('Content-type', 'text/plain; charset=utf-8')]
    start_response(status, headers)
    return [b"Bot is Running via Polling!"]

def main():
    if not BOT_TOKEN:
        print("Bot Token is missing!")
        return
        
    app = Application.builder().token(BOT_TOKEN).build()
    # إضافة معالجات الأوامر والرسائل
    app.add_handler(CommandHandler("start", start_command))
    # معالج الملفات
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL | filters.VIDEO | filters.AUDIO, handle_document))
    # معالج النصوص (عدا الأوامر)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    
    print("Bot is starting polling...")
    # تشغيل البوت بنظام Polling
    app.run_polling(poll_interval=3)

if __name__ == '__main__':
    main()
