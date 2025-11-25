import requests
import time
import json
from flask import Flask, jsonify
import threading

# التوكن الخاص بك مباشرة
TOKEN = "7140828897:AAGQ_jl6-fYI8fdXfS2BXc8l_BGgKrsp0gs"
URL = f"https://api.telegram.org/bot{TOKEN}/getUpdates"

data = {}

# قراءة البيانات السابقة إذا وجدت
try:
    with open("users.json", "r") as f:
        data = json.load(f)
except:
    data = {}

# إعداد Flask لعرض لوحة المستخدمين
app = Flask(__name__)

@app.route("/users")
def get_users():
    return jsonify(data)

@app.route("/count")
def get_count():
    return f"عدد المستخدمين: {len(data)}"

def polling():
    global data
    offset = None
    while True:
        params = {"timeout": 100, "offset": offset}
        updates = requests.get(URL, params=params).json()

        for update in updates.get("result", []):
            offset = update["update_id"] + 1
            msg = update.get("message")
            if not msg:
                continue
            user = msg.get("from", {})
            if msg.get("text") == "/start":
                user_id = str(user["id"])
                if user_id not in data:
                    data[user_id] = {
                        "name": user.get("first_name", ""),
                        "username": user.get("username", ""),
                        "date": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    with open("users.json", "w") as f:
                        json.dump(data, f, indent=4)
        time.sleep(1)

# تشغيل الـ Polling في الخلفية
threading.Thread(target=polling).start()

# تشغيل Flask لعرض لوحة التحكم
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
