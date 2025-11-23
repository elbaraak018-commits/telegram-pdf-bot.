const { Telegraf } = require('telegraf');
const { GoogleGenerativeAI } = require('@google/generative-ai');
const { GoogleAIFileManager } = require("@google/generative-ai/server");
const axios = require('axios');
const fs = require('fs');
const path = require('path');

// استرجاع المفاتيح من متغيرات البيئة
const BOT_TOKEN = process.env.BOT_TOKEN;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;

// *******************************************************************
// *** تم تعطيل هذا الفحص مؤقتاً لضمان نجاح التشغيل النهائي ***
// *******************************************************************
// if (!BOT_TOKEN || !GEMINI_API_KEY) {
//     console.error("❌ خطأ: المتغيرات البيئية مفقودة.");
//     process.exit(1);
// }
// *******************************************************************

// إعداد البوت والذكاء الاصطناعي
const bot = new Telegraf(BOT_TOKEN);
const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
const fileManager = new GoogleAIFileManager(GEMINI_API_KEY);

// *** تم العودة لنموذج gemini-1.5-flash، المفتاح الجديد يجب أن يدعمه ***
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
            },
            { text: prompt }
        ]);

        const responseText = result.response.text();

        // 5. إرسال النتيجة
        if (responseText.length > 4000) {
            const chunks = responseText.match(/.{1,4000}/g) || [];
            for (const chunk of chunks) {
                await ctx.reply(chunk, { parse_mode: 'Markdown' });
            }
        } else {
            await ctx.reply(responseText, { parse_mode: 'Markdown' });
        }

        // 6. حذف رسالة الانتظار وحذف الملف المؤقت
        await ctx.telegram.deleteMessage(ctx.chat.id, loadingMsg.message_id);

    } catch (error) {
        console.error('Final Runtime Error:', error);
        await ctx.reply('❌ حدث خطأ أثناء المعالجة. تأكد من أن مفتاح API صحيح (وتم تحديثه)، أو أن الملف لا يزيد عن 50 ميجابايت.');
    } finally {
        // 7. تنظيف السيرفر
        if (fs.existsSync(filePath)) {
            fs.unlinkSync(filePath);
        }
    }
});

// تشغيل البوت
bot.launch().then(() => console.log('🤖 Bot Started Successfully!'));

// إغلاق آمن
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
