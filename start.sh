#!/usr/bin/env bash
# start.sh — Baş Agent'ı başlatır: ngrok tüneli + Flask sunucu
set -e

# ── Renk kodları ─────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'
info()  { echo -e "${GREEN}✔ $*${NC}"; }
warn()  { echo -e "${YELLOW}⚠ $*${NC}"; }
error() { echo -e "${RED}✘ $*${NC}"; exit 1; }

# ── Ön kontroller ────────────────────────────────────────────────────────────
[ -f .env ] || error ".env bulunamadı — cp .env.example .env yapıp doldurun."
command -v ngrok &>/dev/null || error "ngrok yüklü değil: https://ngrok.com/download"
command -v python3 &>/dev/null || error "python3 bulunamadı."

# ── Bağımlılıklar ────────────────────────────────────────────────────────────
info "Bağımlılıklar kontrol ediliyor..."
pip install -q -r requirements.txt

# ── ngrok başlat ─────────────────────────────────────────────────────────────
info "ngrok başlatılıyor..."
pkill -f "ngrok http" 2>/dev/null || true
ngrok http 5000 --log=stdout > ngrok.log 2>&1 &
NGROK_PID=$!

# URL hazır olana kadar bekle (maks 10 sn)
PUBLIC_URL=""
for i in $(seq 1 20); do
    sleep 0.5
    PUBLIC_URL=$(curl -s http://localhost:4040/api/tunnels \
        | python3 -c "
import sys, json
try:
    tunnels = json.load(sys.stdin).get('tunnels', [])
    https = [t['public_url'] for t in tunnels if t['public_url'].startswith('https')]
    print(https[0] if https else '')
except:
    print('')
" 2>/dev/null)
    [ -n "$PUBLIC_URL" ] && break
done

if [ -z "$PUBLIC_URL" ]; then
    warn "ngrok URL alınamadı — ngrok.log dosyasını kontrol edin."
else
    info "Public URL: $PUBLIC_URL"
    echo ""
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo -e "${YELLOW}  Twilio Webhook URL'si:${NC}"
    echo -e "${GREEN}  $PUBLIC_URL/webhook${NC}"
    echo -e "${YELLOW}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
    echo ""
    echo "  Bu URL'yi şuraya girin:"
    echo "  https://console.twilio.com → WhatsApp Sandbox → When a message comes in"
    echo ""
fi

# ── Flask sunucu başlat ───────────────────────────────────────────────────────
info "Baş Agent başlatılıyor..."
trap "kill $NGROK_PID 2>/dev/null; info 'Durduruldu.'" EXIT
python3 whatsapp_server.py
