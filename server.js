/* server.js */
const express = require("express");
const bodyParser = require("body-parser");
const fetch = require("node-fetch");
const app = express();

app.use(bodyParser.json());

const TELEGRAM_TOKEN = "8213044956:AAFSTaBNirl6xBZuPdYxukGgl7kMtnP11JY";
const PORT = process.env.PORT || 3000;

app.post(`/bot${TELEGRAM_TOKEN}`, async (req, res) => {
  const msg = req.body.message;
  if (!msg || !msg.document) {
    return sendMessage(msg?.chat?.id, "📂 أرسل لي ملف PDF ليتم تلخيصه.");
  }

  const file_id = msg.document.file_id;
  const file_name = msg.document.file_name || "document.pdf";

  sendMessage(msg.chat.id, `📄 تم استلام الملف: <b>${file_name}</b>\n⏳ جاري معالجة الملف...`);

  try {
    const fileRes = await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/getFile?file_id=${file_id}`);
    const data = await fileRes.json();
    if (!data.ok) return sendMessage(msg.chat.id, "❌ فشل الحصول على رابط الملف من Telegram.");

    const file_url = `https://api.telegram.org/file/bot${TELEGRAM_TOKEN}/${data.result.file_path}`;
    const pdfRes = await fetch(file_url);
    const buffer = await pdfRes.arrayBuffer();
    const full_text = extractText(Buffer.from(buffer));

    if (!full_text || full_text.length < 10) return sendMessage(msg.chat.id, "⚠️ لم يتم استخراج نص كافٍ من PDF. تأكد أنه PDF نصي.");

    sendMessage(msg.chat.id, "✅ تم استخراج النص!\n⏳ جاري التلخيص والشرح...");

    const size = 3000;
    for (let i = 0; i < full_text.length; i += size) {
      const part = full_text.substring(i, i + size);
      // إرسال جزء النص مع الشرح والسؤال
      let explanation = part.substring(0, 100) + "...";
      let question = "❓ ما الفكرة الرئيسية في هذا الجزء؟";
      let correct = part.substring(0, 50);
      let wrong1 = part.substring(50, 100);
      let wrong2 = part.substring(100, 150);
      let wrong3 = part.substring(150, 200);

      sendMessage(msg.chat.id,
        `🔹 الجزء ${Math.floor(i/size)+1}:\n${part.substring(0,300)}...\n\n🧠 شرح مبسط:\n${explanation}\n\n${question}\n1️⃣ ${correct}\n2️⃣ ${wrong1}\n3️⃣ ${wrong2}\n4️⃣ ${wrong3}`
      );
    }

  } catch (e) {
    sendMessage(msg.chat.id, "❌ حدث خطأ أثناء معالجة الملف.");
    console.error(e);
  }

  res.sendStatus(200);
});

function extractText(buf) {
  let str = "";
  for (let i = 0; i < buf.length; i++) str += String.fromCharCode(buf[i]);
  return str.replace(/\s+/g, " ").trim();
}

async function sendMessage(chat_id, text) {
  await fetch(`https://api.telegram.org/bot${TELEGRAM_TOKEN}/sendMessage`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ chat_id, text, parse_mode: "HTML" })
  });
}

app.listen(PORT, () => console.log(`Server running on port ${PORT}`));
