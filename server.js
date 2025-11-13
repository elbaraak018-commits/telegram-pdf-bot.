const express = require("express");
const bodyParser = require("body-parser");
const axios = require("axios");
const fs = require("fs");
const PDFParser = require("pdf-parse");
const Tesseract = require("tesseract.js");
const app = express();
const PORT = process.env.PORT || 3000;

app.use(bodyParser.json({ limit: "50mb" }));

// مسار استقبال الملفات من البوت
app.post("/process", async (req, res) => {
  try {
    const { file_url, file_name } = req.body;
    if (!file_url) return res.status(400).json({ error: "file_url مطلوب" });

    // تحميل الملف
    const response = await axios.get(file_url, { responseType: "arraybuffer" });
    const buffer = Buffer.from(response.data);

    let text = "";

    // محاولة استخراج النص من PDF
    try {
      const data = await PDFParser(buffer);
      text = data.text.trim();
    } catch (e) {
      text = "";
    }

    // إذا النص فارغ، استخدم OCR للصور داخل PDF
    if (!text || text.length < 10) {
      const { data: ocrResult } = await Tesseract.recognize(buffer, "eng+ara");
      text = ocrResult.text.trim();
    }

    if (!text || text.length < 10) {
      return res.json({ result: "⚠️ لم يتم استخراج نص كافٍ من الملف." });
    }

    // تقسيم النص لأجزاء
    const size = 2000;
    let parts = [];
    for (let i = 0; i < text.length; i += size) {
      parts.push(text.substring(i, i + size));
    }

    // توليد تلخيص + شرح + أسئلة لكل جزء
    let results = [];
    parts.forEach((part, idx) => {
      const summary = part.substring(0, 150) + "..."; // تلخيص مبسط
      const explanation = part.substring(0, 100) + "...";
      const question = "❓ ما الفكرة الرئيسية في هذا الجزء؟";
      const correct = part.substring(0, 50);
      const wrong1 = part.substring(50, 100);
      const wrong2 = part.substring(100, 150);
      const wrong3 = part.substring(150, 200);

      results.push({
        part: idx + 1,
        summary,
        explanation,
        question,
        answers: [correct, wrong1, wrong2, wrong3]
      });
    });

    res.json({ result: results });
  } catch (err) {
    console.error(err);
    res.status(500).json({ error: "حدث خطأ أثناء معالجة الملف." });
  }
});

// تشغيل السيرفر
app.listen(PORT, () => {
  console.log(`Server running on port ${PORT}`);
});
