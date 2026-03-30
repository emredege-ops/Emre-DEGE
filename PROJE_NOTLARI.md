# Emre-DEGE Proje Notları

## Ne Yapıldı

WhatsApp üzerinden kontrol edilebilen bir AI agent sistemi kuruldu.

### Mimari

```
WhatsApp (Twilio) → ngrok → Flask (whatsapp_server.py)
                                     ↓
                              BasAgent (bas_agent.py)
                                     ↓
                         AgentManager (agent_manager.py)
                                     ↓
                          Alt Agentler (claude_agent_sdk)
```

### Bileşenler

| Dosya | Görev |
|---|---|
| `whatsapp_server.py` | Twilio webhook, Flask, mesaj al/gönder |
| `bas_agent.py` | Ollama (llama3.1:8b) ile baş agent, araç döngüsü |
| `agent_manager.py` | Alt agentleri başlat/durdur/listele |
| `config.py` | .env yükler |
| `start.bat` | Windows başlatma scripti |

### Baş Agent Araçları

- `agent_baslat` — yeni agent başlat (sadece okuma modu)
- `agent_listele` — tüm agentleri listele
- `agent_durum` — belirli bir agentin durumunu sorgula
- `agent_durdur` — çalışan agenti durdur
- `dosya_yaz` — dosya yaz (WhatsApp onayı gerekir: evet/hayır)
- `komut_calistir` — komut çalıştır (WhatsApp onayı gerekir: evet/hayır)

### Güvenlik Kuralları

- Alt agentler SADECE okuma yapabilir (`Read`, `Glob`, `Grep`)
- `dosya_yaz` ve `komut_calistir` öncesi kullanıcıdan WhatsApp onayı alınır
- Sadece `OWNER_WHATSAPP` numarasından gelen mesajlar işlenir

---

## Kurulum (Sıfırdan)

### 1. Gereksinimler

- Python 3.10+
- Ollama (ollama.com) → `llama3.1:8b` modeli
- ngrok hesabı (ngrok.com) — ücretsiz
- Twilio hesabı — WhatsApp Sandbox

### 2. .env Dosyası Oluştur

```
TWILIO_ACCOUNT_SID=ACxxxxxxxx
TWILIO_AUTH_TOKEN=xxxxxxxx
TWILIO_WHATSAPP_FROM=whatsapp:+14155238886
OWNER_WHATSAPP=whatsapp:+905xxxxxxxxx
```

### 3. Ngrok Auth Token

```
ngrok config add-authtoken GERCEK_TOKEN_BURAYA
```
Token: dashboard.ngrok.com → Your Authtoken

### 4. Sistemi Başlat (her açılışta)

```bat
# Terminal 1
ollama serve

# Terminal 2
cd C:\Users\muhasebe4\Desktop\Emre-DEGE
start.bat
```

### 5. Twilio Webhook Ayarla

Her açılışta ngrok URL değişir. `start.bat` URL'yi ekranda gösterir.

Twilio Console → Messaging → Try it out → Send a WhatsApp message → Sandbox settings:
- "When a message comes in": `https://xxxx.ngrok-free.app/webhook`

---

## Bilinen Sorunlar

- llama3.1:8b bazen var olmayan araç adı üretiyor (model sınırlılığı)
- ngrok ücretsiz planda URL her açılışta değişiyor → Twilio'da manuel güncelleme gerekiyor
- Sistem güvenilir çalışmıyor (TODO: debug edilecek)

---

## Sonraki Adımlar (TODO)

- [ ] Sistemi test et, neden güvenilir çalışmadığını bul
- [ ] En azından WhatsApp'tan agent izleme çalışır hale getir
- [ ] Farklı agentler oluştur (scraper, analiz, vb.)
- [ ] ngrok static domain ayarla (Twilio webhook sabit kalsın)

---

## Ortam

- Kullanıcı WhatsApp: `whatsapp:+905447901608`
- Model: Ollama `llama3.1:8b` (ücretsiz, yerel)
- Git branch: `claude/simple-agent-n9TFn`
