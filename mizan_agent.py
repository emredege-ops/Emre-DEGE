"""
Mizan Kontrol Agenti

Belirtilen klasördeki en güncel .xlsx dosyasını okur,
Claude API ile muhasebe kontrolü yapar ve bulguları döner.

Kontroller:
  - Borç toplamı ≠ Alacak toplamı
  - Negatif bakiyeli hesaplar
  - Genel muhasebe değerlendirmesi
"""

import os
from pathlib import Path
from typing import Optional

import anthropic
import pandas as pd

from config import ANTHROPIC_API_KEY, MIZAN_KLASOR

SISTEM_PROMPT = """\
Sen deneyimli bir muhasebe uzmanısın. Sana bir mizan tablosu verilecek.

Şunları kontrol et ve bulgularını raporla:
1. DENGE KONTROLÜ: Borç toplamı = Alacak toplamı mı? Değilse farkı belirt.
2. NEGATİF BAKİYE: Normalde negatif olmaması gereken hesaplarda negatif bakiye var mı?
3. GENEL DEĞERLENDİRME: Dikkat çeken hesaplar, olağandışı durumlar, öneriler.

Eğer her şey yolundaysa bunu da açıkça belirt.

Raporunu TÜRKÇE yaz. Kısa, net ve maddeler halinde yaz.
WhatsApp'tan okunacak — çok uzun olmasın, en önemli bulgulara odaklan.
"""


def _en_guncel_xlsx(klasor: str) -> Optional[Path]:
    """Klasördeki en son değiştirilen .xlsx dosyasını döner."""
    klasor_path = Path(klasor)
    if not klasor_path.exists():
        return None
    dosyalar = list(klasor_path.glob("*.xlsx"))
    if not dosyalar:
        return None
    return max(dosyalar, key=lambda p: p.stat().st_mtime)


def _excel_oku(dosya: Path) -> str:
    """Excel dosyasını okuyup metin özeti döner."""
    try:
        # Tüm sayfaları dene, ilk anlamlı olanı al
        xl = pd.ExcelFile(dosya)
        for sayfa in xl.sheet_names:
            df = xl.parse(sayfa, header=None)
            # Boş satırları at, ilk birkaç satıra bak
            df = df.dropna(how="all")
            if df.shape[0] < 3:
                continue

            # Başlık satırını bul (Borç/Alacak geçen satır)
            baslik_satir = None
            for i, row in df.iterrows():
            	row_str = " ".join(str(v) for v in row.values).lower()
            	if "borç" in row_str or "borc" in row_str or "debit" in row_str:
                	baslik_satir = i
                	break

            if baslik_satir is not None:
                df.columns = df.iloc[baslik_satir]
                df = df.iloc[baslik_satir + 1 :].reset_index(drop=True)
                df = df.dropna(how="all")

            # İlk 200 satır, max 20 sütun
            df = df.iloc[:200, :20]

            # Sayısal sütunları bul ve topla
            ozet_satirlar = []
            numeric_cols = {}
            for col in df.columns:
                try:
                    sayisal = pd.to_numeric(df[col], errors="coerce")
                    toplam = sayisal.sum()
                    if abs(toplam) > 0:
                        numeric_cols[str(col)] = float(toplam)
                except Exception:
                    pass

            if numeric_cols:
                ozet_satirlar.append(f"=== SAYFA: {sayfa} ===")
                ozet_satirlar.append("SÜTUN TOPLAMLARI:")
                for k, v in numeric_cols.items():
                    ozet_satirlar.append(f"  {k}: {v:,.2f}")

            # Ham tablo (ilk 80 satır)
            ozet_satirlar.append(f"\nVERİ (ilk {min(80, len(df))} satır):")
            ozet_satirlar.append(df.head(80).to_string(index=False, na_rep=""))

            return "\n".join(ozet_satirlar)

        # Hiç uygun sayfa bulunamadıysa ham oku
        df = pd.read_excel(dosya, header=None).dropna(how="all")
        return f"HAM VERİ:\n{df.head(100).to_string(index=False, na_rep='')}"

    except Exception as exc:
        return f"Dosya okuma hatası: {exc}"


def mizan_kontrol_et(klasor: Optional[str] = None) -> str:
    """
    Mizan dosyasını okur ve Claude ile analiz eder.
    Bulguları metin olarak döner (WhatsApp'a gönderilecek).
    """
    hedef_klasor = klasor or MIZAN_KLASOR

    if not hedef_klasor:
        return (
            "⚠️ Mizan klasörü ayarlanmamış.\n"
            ".env dosyasına MIZAN_KLASOR=C:\\...\\mizan_klasoru ekleyin."
        )

    if not ANTHROPIC_API_KEY:
        return (
            "⚠️ Anthropic API anahtarı eksik.\n"
            ".env dosyasına ANTHROPIC_API_KEY=sk-... ekleyin."
        )

    dosya = _en_guncel_xlsx(hedef_klasor)
    if dosya is None:
        return f"⚠️ '{hedef_klasor}' klasöründe .xlsx dosyası bulunamadı."

    tablo_metni = _excel_oku(dosya)

    # Claude API çağrısı
    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        mesaj = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1024,
            system=SISTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"Dosya adı: {dosya.name}\n\n"
                        f"{tablo_metni}"
                    ),
                }
            ],
        )
        analiz = mesaj.content[0].text
    except Exception as exc:
        return f"❌ Claude API hatası: {exc}"

    return f"📊 *Mizan Raporu* — {dosya.name}\n\n{analiz}"
