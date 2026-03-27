"""
WhatsApp Webhook Sunucusu — Twilio üzerinden mesaj alır, Baş Agent'a iletir.

Akış:
  1. Twilio → POST /webhook
  2. Gelen mesaj Baş Agent'a gönderilir (ayrı thread)
  3. Yanıt Twilio API ile WhatsApp'a gönderilir
  4. Agent tamamlandığında otomatik bildirim gider
"""

import threading

from flask import Flask, Response, request
from twilio.rest import Client
from twilio.twiml.messaging_response import MessagingResponse

from agent_manager import AgentInfo, AgentManager
from bas_agent import BasAgent
from config import (
    OWNER_WHATSAPP,
    TWILIO_ACCOUNT_SID,
    TWILIO_AUTH_TOKEN,
    TWILIO_WHATSAPP_FROM,
)

app = Flask(__name__)
twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


# ------------------------------------------------------------------ #
# Yardımcı: WhatsApp mesajı gönder (1600 karakter parçalayarak)       #
# ------------------------------------------------------------------ #

def send_whatsapp(to: str, text: str) -> None:
    max_len = 1500
    parts = [text[i : i + max_len] for i in range(0, len(text), max_len)]
    for part in parts:
        twilio.messages.create(from_=TWILIO_WHATSAPP_FROM, to=to, body=part)


# ------------------------------------------------------------------ #
# Agent tamamlanma bildirimi                                           #
# ------------------------------------------------------------------ #

def on_agent_done(agent_id: str, info: AgentInfo) -> None:
    if info.status == "completed":
        snippet = (info.result or "")[:800]
        msg = f"✅ *{agent_id}* tamamlandı!\n\n{snippet}"
    elif info.status == "failed":
        msg = f"❌ *{agent_id}* başarısız!\n\nHata: {info.error}"
    else:
        return  # stopped → bildirim gönderme

    send_whatsapp(OWNER_WHATSAPP, msg)


# ------------------------------------------------------------------ #
# Singleton bileşenler                                                 #
# ------------------------------------------------------------------ #

manager = AgentManager(notify_callback=on_agent_done)
agent   = BasAgent(agent_manager=manager, whatsapp_sender=send_whatsapp)


# ------------------------------------------------------------------ #
# Webhook endpoint                                                     #
# ------------------------------------------------------------------ #

@app.route("/webhook", methods=["POST"])
def webhook():
    from_number  = request.form.get("From", "")
    incoming_msg = request.form.get("Body", "").strip()

    # Sadece sahibine yanıt ver
    if from_number != OWNER_WHATSAPP:
        return Response(status=403)

    if not incoming_msg:
        return str(MessagingResponse())

    # Mesajı arka planda işle (Flask sync'i bloklamamak için)
    def process():
        try:
            reply = agent.process_message(incoming_msg)
            send_whatsapp(from_number, reply)
        except Exception as exc:
            send_whatsapp(from_number, f"⚠️ Hata: {exc}")

    threading.Thread(target=process, daemon=True).start()

    # Twilio'ya hemen boş TwiML dön (çift mesaj önlemek için)
    return str(MessagingResponse())


# ------------------------------------------------------------------ #
# Başlat                                                               #
# ------------------------------------------------------------------ #

if __name__ == "__main__":
    print("🤖 Baş Agent hazır")
    print(f"📱 Bildirimler → {OWNER_WHATSAPP}")
    print("🌐 Webhook dinleniyor: http://0.0.0.0:5000/webhook")
    app.run(host="0.0.0.0", port=5000, debug=False)
