const { Telegraf } = require('telegraf');
const { GoogleGenAI } = require('@google/genai');
const axios = require('axios');
const pdf = require('pdf-parse');

// استرجاع المفاتيح بأمان من متغيرات البيئة
const BOT_TOKEN = process.env.BOT_TOKEN;
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;

if (!BOT_TOKEN || !GEMINI_API_KEY) {
    console.error("❌ خطأ: يرجى تعيين متغيرات البيئة (BOT_TOKEN و GEMINI_API_KEY).");
    process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);
const ai = new GoogleGenAI({ apiKey: GEMINI_API_KEY });

bot.start((ctx) => {
    ctx.reply('أهلاً بك! أنا بوت مُحلل ملفات PDF باستخدام الذكاء الاصطناعي.\n\nأرسل لي ملف PDF وسأقوم بتلخيصه وإنشاء أسئلة وتمارين منه.');
});

bot.on('document', async (ctx) => {
    if (ctx.message.document.mime_type !== 'application/pdf') {
        return ctx.reply('⚠️ يُرجى إرسال ملف بصيغة PDF فقط.');
    }

    const chatId = ctx.chat.id;
    const fileId = ctx.message.document.file_id;
    
    const loadingMsg = await ctx.reply('⏳ جاري تحميل ومعالجة ملف PDF... قد يستغرق هذا بضع لحظات.');

    try {
        // 1. الحصول على رابط تنزيل الملف
        const fileLink = await ctx.telegram.getFileLink(fileId);
        
        // 2. تنزيل الملف كـ Buffer
        const response = await axios.get(fileLink.href, { responseType: 'arraybuffer' });
        const pdfBuffer = Buffer.from(response.data);

        // 3. استخراج النص
        const data = await pdf(pdfBuffer);
        const pdfText = data.text;

        if (pdfText.length < 50) {
            await ctx.telegram.deleteMessage(chatId, loadingMsg.message_id);
            return ctx.reply('⚠️ فشل استخراج النص أو أن الملف قصير جدًا.');
        }

        // 4. بناء التعليمات (الـ Prompt)
        const prompt = `أنت مُعلم خبير. مهمتك هي تحليل النص التالي (المستخرج من ملف PDF). قم بإنشاء ملخص شامل، يليه 3 أسئلة اختيار من متعدد مع الإجابات الصحيحة، ثم مثالين تطبيقيين قصيرين، وثلاثة تمارين تدريبية قصيرة ليقوم الطالب بحلها. نظم الإجابة في أقسام واضحة. النص هو: \n\n---النص---\n${pdfText}`;

        // 5. استدعاء Gemini API
        const aiResponse = await ai.models.generateContent({
            model: "gemini-2.5-flash", 
            contents: prompt,
        });

        // 6. إرسال النتيجة
        await ctx.telegram.deleteMessage(chatId, loadingMsg.message_id);
        ctx.reply('✅ **تم التحليل بنجاح!**\n\n' + aiResponse.text, { parse_mode: 'Markdown' });
        
    } catch (error) {
        console.error('Error processing PDF:', error);
        await ctx.telegram.deleteMessage(chatId, loadingMsg.message_id); 
        ctx.reply('❌ حدث خطأ أثناء معالجة الملف أو الاتصال بالذكاء الاصطناعي.');
    }
});

bot.launch().then(() => {
    console.log('🤖 Bot has started successfully!');
});

process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));
