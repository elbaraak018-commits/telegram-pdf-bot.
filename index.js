const { Telegraf } = require('telegraf');
const { GoogleGenerativeAI } = require('@google/generative-ai');
const { GoogleAIFileManager } = require("@google/generative-ai/server");
const axios = require('axios');
const fs = require('fs');
const path = require('path');

// استرجاع المفاتيح
const BOT_TOKEN = process.env.BOT_TOKEN;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;

if (!BOT_TOKEN || !GEMINI_API_KEY) {
    console.error("❌ خطأ: المتغيرات البيئية مفقودة.");
    process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);
const genAI = new GoogleGenerativeAI(GEMINI_API_KEY);
const fileManager = new GoogleAIFileManager(GEMINI_API_KEY);
const model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" });

bot.start((ctx) => {
    ctx.reply('أهلاً بك! 🤖\nأرسل لي أي ملف PDF (سواء كان نصاً أو ممسوحاً ضوئياً "سكانر") وسأقوم بشرحه وعمل اختبار لك.');
});

bot.on('document', async (ctx) => {
    // 1. التحقق من نوع الملف
    if (ctx.message.document.mime_type !== 'application/pdf') {
        return ctx.reply('⚠️ يُرجى إرسال ملف بصيغة PDF فقط.');
    }

    const loadingMsg = await ctx.reply('⏳ جاري استلام الملف ورفعه للذكاء الاصطناعي... (قد يستغرق وقتاً حسب حجم الملف)');
    const filePath = path.join(__dirname, `temp_${ctx.message.document.file_id}.pdf`);

    try {
        // 2. تنزيل الملف من تليجرام وحفظه محلياً بشكل مؤقت
        const fileLink = await ctx.telegram.getFileLink(ctx.message.document.file_id);
        const response = await axios({
            url: fileLink.href,
            method: 'GET',
            responseType: 'stream'
        });

        const writer = fs.createWriteStream(filePath);
        response.data.pipe(writer);

        // انتظار انتهاء التحميل
        await new Promise((resolve, reject) => {
            writer.on('finish', resolve);
            writer.on('error', reject);
        });

        // 3. رفع الملف إلى Google Gemini
        const uploadResult = await fileManager.uploadFile(filePath, {
            mimeType: "application/pdf",
            displayName: "User PDF Document",
        });

        await ctx.telegram.editMessageText(ctx.chat.id, loadingMsg.message_id, null, '🧠 جاري قراءة وتحليل المحتوى (النص والصور)...');

        // 4. إرسال الطلب إلى Gemini
        const prompt = `أنت معلم خبير. لقد أرفقت لك ملف PDF (قد يحتوي على نصوص أو صور ممسوحة ضوئياً). 
        مهمتك هي قراءة المحتوى بالكامل والقيام بالآتي:
        1. شرح مبسط وشامل للأفكار الرئيسية.
        2. تقديم 3 أمثلة توضيحية.
        3. كتابة 5 أسئلة اختيار من متعدد (مع تحديد الإجابة الصحيحة في النهاية).
        4. وضع تمرين بسيط للطالب.
        
        يرجى تنسيق الإجابة بشكل جميل وواضح.`;

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

        // 5. إرسال الرد للمستخدم
        // تقسيم الرسالة إذا كانت طويلة جداً (تليجرام يقبل 4096 حرف كحد أقصى)
        if (responseText.length > 4000) {
             const chunks = responseText.match(/.{1,4000}/g);
             for (const chunk of chunks) {
                 await ctx.reply(chunk, { parse_mode: 'Markdown' });
             }
        } else {
             await ctx.reply(responseText, { parse_mode: 'Markdown' });
        }

        // حذف رسالة الانتظار
        await ctx.telegram.deleteMessage(ctx.chat.id, loadingMsg.message_id);

    } catch (error) {
        console.error('Error:', error);
        await ctx.reply('❌ حدث خطأ. قد يكون الملف كبيراً جداً أو محمياً، أو حدث خطأ في الاتصال.');
    } finally {
        // 6. تنظيف: حذف الملف المؤقت من السيرفر
        if (fs.existsSync(filePath)) {
            fs.unlinkSync(filePath);
        }
    }
});

bot.launch().then(() => console.log('🤖 Bot Started with Vision Capabilities!'));

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
