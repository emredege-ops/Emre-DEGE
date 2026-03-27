@echo off
chcp 65001 >nul
title Baş Agent

echo.
echo  ========================================
echo   Baş Agent başlatılıyor...
echo  ========================================
echo.

:: ── .env kontrolü ────────────────────────────────────────────────────────────
if not exist .env (
    echo  [HATA] .env dosyası bulunamadı!
    echo  Çözüm: .env.example dosyasını .env olarak kopyalayın ve doldurun.
    pause
    exit /b 1
)

:: ── ngrok kontrolü (Microsoft Store veya PATH'teki ngrok) ───────────────────
where ngrok >nul 2>&1
if errorlevel 1 (
    if not exist ngrok.exe (
        echo  [HATA] ngrok bulunamadı!
        echo  Çözüm: Microsoft Store'dan ngrok'u açın veya https://ngrok.com/download
        pause
        exit /b 1
    )
    set NGROK_CMD=ngrok.exe
) else (
    set NGROK_CMD=ngrok
)

:: ── Bağımlılıklar ─────────────────────────────────────────────────────────────
echo  [1/3] Python bağımlılıkları kuruluyor...
python -m pip install -q -r requirements.txt
if errorlevel 1 (
    echo  [HATA] pip install başarısız!
    pause
    exit /b 1
)
echo  Tamam.
echo.

:: ── ngrok başlat ─────────────────────────────────────────────────────────────
echo  [2/3] ngrok başlatılıyor...
taskkill /f /im ngrok.exe >nul 2>&1
start /min "" %NGROK_CMD% http 5000

:: URL hazır olana kadar bekle
set PUBLIC_URL=
for /l %%i in (1,1,20) do (
    timeout /t 1 /nobreak >nul
    for /f "delims=" %%u in ('python -c "import urllib.request,json; d=json.loads(urllib.request.urlopen(\"http://localhost:4040/api/tunnels\").read()); https=[t[\"public_url\"] for t in d[\"tunnels\"] if t[\"public_url\"].startswith(\"https\")]; print(https[0] if https else \"\")" 2^>nul') do (
        set PUBLIC_URL=%%u
    )
    if defined PUBLIC_URL goto :got_url
)

:got_url
if not defined PUBLIC_URL (
    echo  [UYARI] ngrok URL alinamadi. ngrok penceresini kontrol edin.
) else (
    echo.
    echo  ========================================
    echo   Twilio Webhook URL:
    echo   %PUBLIC_URL%/webhook
    echo  ========================================
    echo.
    echo  Bu URL'yi Twilio'ya girin:
    echo  console.twilio.com - WhatsApp Sandbox
    echo  "When a message comes in" alanina yapistirin.
    echo.
)

:: ── Flask sunucu başlat ───────────────────────────────────────────────────────
echo  [3/3] Baş Agent başlatılıyor...
echo  Durdurmak için: Ctrl+C
echo.
python whatsapp_server.py

pause
