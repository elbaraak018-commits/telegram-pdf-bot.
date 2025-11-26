# ---------------------------------------------
# app.py — Telegram Bot + WSGI HealthCheck
# يعمل 100% على Render بدون Worker خارجي
# ---------------------------------------------

import os
import threading
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from google import genai
from google.genai import types

# --------------------
# 1. قراءة المتغيرات
# --------------------
BOT_TOKEN = os.getenv("BOT_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

COMPREHENSIVE_PROMPT = """
أنت بوت معلم ومساعد دراسي ذكي...
"""  # اختصار، اترك نصك كما هو

# --------------------
# 2. Handlers
# --------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📘 أهلاً! أرسل ملف PDF أو صورة أو فيديو وسأقوم بتحليلها لك."
    )

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not client:
        await update.message.reply_text("مفتاح GEMINI مفقود ❌")
        return

    user_text = update.message.text
    await update.message.reply_text("⏳ جاري التفكير...")

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=[user_text]
        )
        await update.message.reply_text(response.text)
    except Exception as e:
        await update.message.reply_text(f"خطأ أثناء الرد: {e}")

async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not client:
        await update.message.reply_text("مفتاح GEMINI غير موجود ❌")
        return

    await update.message.reply_text("📥 جاري تحليل الملف...")

    file_info = update.message.document or update.message.photo[-1]

    file = await context.bot.get_file(file_info.file_id)
    file_path = f"/tmp/{file_info.file_unique_id}"

    await file.download_to_drive(file_path)

    try:
        uploaded_file = client.files.upload(file=file_path)
        contents = [COMPREHENSIVE_PROMPT, uploaded_file]

        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=contents
        )

        await update.message.reply_text(response.text)

        client.files.delete(name=uploaded_file.name)

    except Exception as e:
        await update.message.reply_text(f"❌ خطأ: {e}")
    finally:
        if os.path.exists(file_path):
            os.remove(file_path)


# ---------------------------
# 3. تشغيل البوت في Thread
# ---------------------------
def run_bot():
    print("🚀 Telegram Bot is running (Polling)...")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.Document.ALL | filters.PHOTO, handle_document))

    app.run_polling(poll_interval=3)


# نشغّل البوت في Thread حتى لا يمنع تشغيل Web Server
threading.Thread(target=run_bot, daemon=True).start()


# ---------------------------
# 4. هذا هو Web Server الحقيقي (WSGI)
# الذي يحتاجه Render لنجاح Health Check
# ---------------------------
def app(environ, start_response):
    """WSGI app required by Render"""
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [b"Bot is Running Successfully!"]


# ---------------------------
# 5. تشغيل محلي فقط (بدون Render)
# ---------------------------
if __name__ == "__main__":
    from waitress import serve
    print("🌐 Starting Local Server on port 8000...")
    serve(app, host="0.0.0.0", port=8000)
