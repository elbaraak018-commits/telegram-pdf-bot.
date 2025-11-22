const { Telegraf } = require('telegraf');
const { GoogleGenerativeAI } = require('@google/generative-ai');
const { GoogleAIFileManager } = require("@google/generative-ai/server");
const axios = require('axios');
const fs = require('fs');
const path = require('path');

// استرجاع المفاتيح من متغيرات البيئة
const BOT_TOKEN = process.env.BOT_TOKEN;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;

// التحقق من وجود المفاتيح
if (!BOT_TOKEN || !GEMINI_API_KEY) {
    console.error("❌ خطأ: يرجى التأكد من إضافة BOT_TOKEN و GEMINI_API_KEY في إعدادات Render.");
    process.exit(1);
}

// إعداد البوت والذكاء الاصطناعي
const bot = new Telegraf(BOT_TOKEN);
const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
const fileManager = new GoogleAIFileManager(GEMINI_API_KEY);
const model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" });

// رسالة الترحيب
bot.start((ctx) => {
    ctx.reply('أهلاً بك! 🤖\nأرسل لي ملف PDF (نصي أو ممسوح ضوئياً) وسأقوم بشرحه وعمل اختبار لك.');
});

// معالجة الملفات
bot.on('document', async (ctx) => {
    // 1. التحقق من نوع الملف
    if (ctx.message.document.mime_type !== 'application/pdf') {
        return ctx.reply('⚠️ عذراً، أنا أقبل ملفات PDF فقط.');
    }

    const loadingMsg = await ctx.reply('⏳ جاري استلام الملف ورفعه للتحليل... (انتظر قليلاً)');
    
    // تحديد مسار مؤقت للملف
    const filePath = path.join(__dirname, `temp_${ctx.message.document.file_id}.pdf`);

    try {
        // 2. الحصول على رابط الملف وتنزيله
        const fileLink = await ctx.telegram.getFileLink(ctx.message.document.file_id);
        const response = await axios({
            url: fileLink.href,
            method: 'GET',
            responseType: 'stream'
        });

        // حفظ الملف محلياً
        const writer = fs.createWriteStream(filePath);
        response.data.pipe(writer);

        // انتظار اكتمال التنزيل
        await new Promise((resolve, reject) => {
            writer.on('finish', resolve);
            writer.on('error', reject);
        });

        // 3. رفع الملف إلى Google Gemini
        const uploadResult = await fileManager.uploadFile(filePath, {
            mimeType: "application/pdf",
            displayName: "User PDF",
        });

        await ctx.telegram.editMessageText(ctx.chat.id, loadingMsg.message_id, null, '🧠 جاري قراءة المحتوى (نصوص وصور) وتوليد الشرح...');

        // 4. إرسال الأمر إلى Gemini
        const prompt = `
        أنت معلم ذكي. لقد أرفقت لك ملف PDF.
        المطلوب منك:
        1. شرح ملخص للأفكار الرئيسية في الملف.
        2. كتابة 3 أمثلة توضيحية.
        3. كتابة 5 أسئلة اختيار من متعدد (MCQ) مع توضيح الإجابة الصحيحة في الأسفل.
        4. تمرين بسيط للطالب.
        
        اجعل لغتك واضحة وتنسيقك مرتباً.
        `;

        const result = await model.generateContent([
            {
                fileData: {
                    mimeType: uploadResult.file.mimeType,
                    fileUri: uploadResult.file.uri
    }
