const { Telegraf } = require('telegraf');
const { GoogleGenerativeAI } = require('@google/generative-ai'); // المكتبة الصحيحة
const axios = require('axios');
const pdf = require('pdf-parse');

// استرجاع المفاتيح
const BOT_TOKEN = process.env.BOT_TOKEN;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;

if (!BOT_TOKEN || !GEMINI_API_KEY) {
    console.error("❌ خطأ: المتغيرات البيئية مفقودة.");
    process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);
const genAI = new GoogleGenerativeAI(GEMINI_API_KEY); // طريقة الاتصال الجديدة
const model = genAI.getGenerativeModel({ model: "gemini-1.5-flash" }); // استخدام موديل مستقر

bot.start((ctx) => {
    ctx.reply('أهلاً بك! 🤖\nأرسل لي ملف PDF وسأقوم بتحليله باستخدام Gemini.');
});

bot.on('document', async (ctx) => {
    if (ctx.message.document.mime_type !== 'application/pdf') {
        return ctx.reply('⚠️ يُرجى إرسال ملف بصيغة PDF فقط.');
    }

    const chatId = ctx.chat.id;
    const fileId = ctx.message.document.file_id;
    const loadingMsg = await ctx.reply('⏳ جاري تحميل ومعالجة الملف...');

    try {
        const fileLink = await ctx.telegram.getFileLink(fileId);
        const response = await axios.get(fileLink.href, { responseType: 'arraybuffer' });
        const pdfBuffer = Buffer.from(response.data);

        // استخراج النص
        const data = await pdf(pdfBuffer);
        const pdfText = data.text;

        if (pdfText.length < 50) {
            await ctx.telegram.deleteMessage(chatId, loadingMsg.message_id);
            return ctx.reply('⚠️ الملف قصير جداً أو فارغ.');
        }

        // تجهيز السؤال لـ Gemini
        const prompt = `قم بتلخيص النص التالي وإنشاء 3 أسئلة اختيار من متعدد مع الإجابات:\n\n${pdfText}`;

        // استدعاء Gemini بالطريقة الجديدة
        const result = await model.generateContent(prompt);
        const responseText = result.response.text();

        await ctx.telegram.deleteMessage(chatId, loadingMsg.message_id);
        ctx.reply(responseText, { parse_mode: 'Markdown' });

    } catch (error) {
        console.error('Error:', error);
        // محاولة مسح رسالة التحميل إذا كانت موجودة
        try { await ctx.telegram.deleteMessage(chatId, loadingMsg.message_id); } catch (e) {}
        ctx.reply('❌ حدث خطأ أثناء المعالجة. تأكد أن المفاتيح صحيحة وأن الملف سليم.');
    }
});

bot.launch().then(() => console.log('🤖 Bot Started!'));

// إغلاق آمن
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
