import os
import logging
import mimetypes
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from google import genai
import time # 👈 تم إضافة مكتبة الوقت للانتظار

# -----------------
# 1. إعداد السجلات والثوابت
# -----------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
MAX_TELEGRAM_MESSAGE_LENGTH = 4096 
MAX_WAIT_TIME = 180 # 🔑 زيادة المهلة إلى 3 دقائق (180 ثانية) للملفات الكبيرة جداً

# -----------------
# 2. وظيفة تقسيم النص (حل مشكلة "Text is too long")
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
    """معالجة الملفات (صور، فيديو، صوت، وثائق)"""
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
            elif update.message.video: mime_type = 'video/mp4'
            elif update.message.audio: mime_type = 'audio/mp3'
            else: mime_type = 'application/pdf'

        logging.info(f"Processing file: {file_path} with type: {mime_type}")

        # 3. رفع الملف إلى Gemini
        uploaded_file = client.files.upload(
            file=file_path,
            config={'mime_type': mime_type}
