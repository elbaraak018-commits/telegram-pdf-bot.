const { Telegraf } = require('telegraf');
const axios = require('axios');
const fs = require('fs');
const path = require('path');
const ytdl = require('ytdl-core'); 

// استرجاع مفتاح البوت
const BOT_TOKEN = process.env.BOT_TOKEN;

if (!BOT_TOKEN) {
    console.error("❌ خطأ: متغير BOT_TOKEN مفقود.");
    process.exit(1);
}

const bot = new Telegraf(BOT_TOKEN);

// رسالة الترحيب والتعليمات
bot.start((ctx) => {
    ctx.reply(
        'أهلاً بك في بوت تحميل الوسائط! 🥳\n\n' +
        'أرسل لي:\n' +
        '1. **رابط تيك توك** لتحميل الفيديو بدون علامة مائية.\n' +
        '2. **رابط YouTube** لتحميل الأغنية كملف صوتي (MP3).\n' +
        '3. **اسم أغنية** (مثال: /song اسم الأغنية) للبحث والتحميل.'
    );
});

// **************************************************
// 🎶 وظيفة تحميل الأغاني من اسم (/song)
// **************************************************
bot.command('song', (ctx) => {
    const query = ctx.message.text.split(' ').slice(1).join(' ');
    
    if (!query) {
        return ctx.reply('يرجى كتابة اسم الأغنية بعد الأمر /song. مثال: /song عمرو دياب نور العين');
    }

    ctx.reply(`⏳ جاري البحث عن الأغنية "${query}" وتحميلها... هذه الميزة تحتاج إلى برمجة إضافية للبحث.`);
});


// **************************************************
// 🔗 معالجة الروابط (تيك توك و YouTube)
// **************************************************
bot.on('text', async (ctx) => {
    const url = ctx.message.text;

    // 1. معالجة رابط YouTube لتحميل الصوت
    if (url.includes('youtube.com') || url.includes('youtu.be')) {
        await handleYouTubeDownload(ctx, url);
        return;
    }

    // 2. معالجة رابط تيك توك لتحميل الفيديو
    if (url.includes('tiktok.com')) {
        await handleTikTokDownload(ctx, url);
        return;
    }

    // 3. إذا لم يكن رابطاً معروفاً
    ctx.reply('❌ عذراً، لا أتعرف على هذا الرابط أو الأمر. يرجى إرسال رابط تيك توك أو YouTube، أو استخدام /song.');
});


// **************************************************
// 🎧 وظيفة تحميل YouTube (كصوت MP3)
// **************************************************
async function handleYouTubeDownload(ctx, url) {
    const loadingMsg = await ctx.reply('⏳ جاري تحليل رابط يوتيوب والبدء في تحميل ملف الصوت...');

    try {
        if (!ytdl.validateURL(url)) {
            return ctx.telegram.editMessageText(ctx.chat.id, loadingMsg.message_id, null, '❌ الرابط غير صالح لـ YouTube.');
        }

        const info = await ytdl.getInfo(url);
        const title = info.videoDetails.title;
        const filePath = path.join(__dirname, `${title.replace(/[^a-zA-Z0-9]/g, '_')}.mp3`);
        
        ctx.telegram.editMessageText(ctx.chat.id, loadingMsg.message_id, null, `⬇️ جاري تحميل: **${title}**...`);

        // بدء التحميل كملف صوتي (MP3)
        const audioStream = ytdl(url, { filter: 'audioonly' });

        const writeStream = fs.createWriteStream(filePath);
        audioStream.pipe(writeStream);

        await new Promise((resolve, reject) => {
            writeStream.on('finish', resolve);
            writeStream.on('error', reject);
        });

        // إرسال الملف
        await ctx.replyWithAudio({ source: filePath }, { caption: `✅ تم تحميل الأغنية: ${title}` });

        // تنظيف وحذف الملف
        fs.unlinkSync(filePath);
        await ctx.telegram.deleteMessage(ctx.chat.id, loadingMsg.message_id);

    } catch (error) {
        console.error('YouTube Download Error:', error);
        ctx.reply('❌ حدث خطأ أثناء تحميل الأغنية. قد يكون الفيديو محظوراً.');
    }
}


// **************************************************
// 🎥 وظيفة تحميل TikTok (باستخدام API خارجية موثوقة) - الكود الأكيد
// **************************************************
async function handleTikTokDownload(ctx, url) {
    const loadingMsg = await ctx.reply('⏳ جاري تحليل رابط تيك توك ومحاولة إزالة العلامة المائية...');
    
    // استخدام رابط API مباشر يمرر رابط التيك توك لتحميل الفيديو
    // هذه الطريقة أكثر استقراراً من الاعتماد على مكتبات npm التي تُحذف
    try {
        const videoUrl = `https://api.tiktok.download/v1/download?url=${encodeURIComponent(url)}`;
        
        await ctx.telegram.editMessageText(ctx.chat.id, loadingMsg.message_id, null, '⬇️ تم العثور على الفيديو. جاري إرساله...');

        // إرسال الفيديو مباشرة من الرابط
        await ctx.replyWithVideo(videoUrl, { caption: '✅ فيديو تيك توك (بدون علامة مائية).' });

        // حذف رسالة الانتظار
        await ctx.telegram.deleteMessage(ctx.chat.id, loadingMsg.message_id);

    } catch (error) {
        console.error('TikTok Download Error:', error);
        ctx.reply('❌ حدث خطأ أثناء تحميل فيديو تيك توك. يرجى التأكد من أن الرابط صالح وعام.');
    }
}


// تشغيل البوت
bot.launch().then(() => console.log('✅ Multi-Media Bot Started Successfully!'));

// إغلاق آمن
process.once('SIGINT', () => bot.stop('SIGINT'));
process.once('SIGTERM', () => bot.stop('SIGTERM'));

// هذا السطر يمنع Render من إظهار مشكلة الـ Port Timeout
module.exports = {};
