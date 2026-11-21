"""
Mizan Kural Kontrolü (kural tabanlı, LLM gerektirmez)

Şirketlerin gönderdiği en güncel mizanları okur ve Tekdüzen Hesap Planı'na
göre şunları çıkarır:

  1. TERS BAKİYE   : Normal bakiye yönünün tersine çalışan hesaplar
                     (ör. 120 alacak bakiye, 320 borç bakiye, 100 kasa alacak).
  2. CARİ VİRMAN   : Aynı cari hem 120 Alıcılar hem 320 Satıcılar'da (veya
                     120-340, 159-320, 131-331, 136-336 ...) bakiyeli ise
                     birbirine virman/mahsup önerisi.
                     Aynı cari aynı ana hesapta birden fazla alt hesapta zıt
                     bakiyeli ise (120.01 borç / 120.02 alacak) iç virman.
  3. DÖNEM SONU    : 191-391 KDV mahsubu gibi kapatılması gereken çiftler.
  4. DENGE         : Borç toplamı = Alacak toplamı kontrolü.

Klasör yapısı:
  MIZAN_KLASOR/
      SirketA/  mizan_ekim.xlsx, mizan_kasim.xlsx   -> her alt klasörün en günceli
      SirketB/  ...
      SirketC_mizan.xlsx                            -> kök dizindeki her dosya ayrı şirket

Kullanım:
  python mizan_kontrol.py <klasor_veya_dosya> [-o rapor.xlsx]
"""

from __future__ import annotations

import argparse
import difflib
import os
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd

TOLERANS = 0.01  # kuruş altı farklar yok sayılır

# --------------------------------------------------------------------------- #
# Tekdüzen Hesap Planı — normal bakiye yönü                                   #
# --------------------------------------------------------------------------- #
# "B" = borç bakiye vermeli, "A" = alacak bakiye vermeli, None = kontrol dışı

# Aktif hesaplar içinde ALACAK çalışan (düzenleyici / karşılık) hesaplar
_AKTIF_ALACAK = {
    "103", "119", "122", "124", "129", "137", "139", "158", "199",
    "222", "224", "229", "237", "239", "241", "243", "244", "246", "247", "249",
    "257", "268", "278", "298", "299",
}
# Pasif hesaplar içinde BORÇ çalışan (düzenleyici) hesaplar
_PASIF_BORC = {
    "302", "308", "322", "337", "371",
    "402", "408", "422", "437",
    "501", "503", "580", "591",
}
# Gelir tablosu
_GELIR_BORC = {"610", "611", "612"}
_KONTROL_DISI = {"690", "692", "697", "698", "590"}  # dönem sonu hesapları

HESAP_ADLARI = {
    "100": "Kasa", "101": "Alınan Çekler", "102": "Bankalar", "103": "Verilen Çekler ve Ödeme Emirleri",
    "108": "Diğer Hazır Değerler", "120": "Alıcılar", "121": "Alacak Senetleri",
    "126": "Verilen Depozito ve Teminatlar", "127": "Diğer Ticari Alacaklar",
    "131": "Ortaklardan Alacaklar", "135": "Personelden Alacaklar", "136": "Diğer Çeşitli Alacaklar",
    "150": "İlk Madde ve Malzeme", "153": "Ticari Mallar", "159": "Verilen Sipariş Avansları",
    "180": "Gelecek Aylara Ait Giderler", "190": "Devreden KDV", "191": "İndirilecek KDV",
    "193": "Peşin Ödenen Vergiler ve Fonlar", "195": "İş Avansları", "196": "Personel Avansları",
    "300": "Banka Kredileri", "320": "Satıcılar", "321": "Borç Senetleri",
    "326": "Alınan Depozito ve Teminatlar", "331": "Ortaklara Borçlar", "335": "Personele Borçlar",
    "336": "Diğer Çeşitli Borçlar", "340": "Alınan Sipariş Avansları",
    "360": "Ödenecek Vergi ve Fonlar", "361": "Ödenecek Sosyal Güvenlik Kesintileri",
    "368": "Vadesi Geçmiş Ertelenmiş veya Taksitlendirilmiş Vergi ve Diğer Yük.",
    "370": "Dönem Kârı Vergi ve Diğer Yasal Yükümlülük Karşılıkları",
    "371": "Dönem Kârının Peşin Ödenen Vergi ve Diğer Yükümlülükleri", "391": "Hesaplanan KDV",
    "500": "Sermaye", "501": "Ödenmemiş Sermaye", "570": "Geçmiş Yıllar Kârları",
    "580": "Geçmiş Yıllar Zararları", "590": "Dönem Net Kârı", "591": "Dönem Net Zararı",
    "600": "Yurt İçi Satışlar", "601": "Yurt Dışı Satışlar", "610": "Satıştan İadeler",
    "611": "Satış İskontoları", "770": "Genel Yönetim Giderleri",
}


def normal_yon(ana: str) -> Optional[str]:
    """3 haneli ana hesap kodu için normal bakiye yönü."""
    if len(ana) != 3 or not ana.isdigit():
        return None
    if ana in _KONTROL_DISI:
        return None
    g = ana[0]
    if g in "12":
        return "A" if ana in _AKTIF_ALACAK else "B"
    if g in "345":
        return "B" if ana in _PASIF_BORC else "A"
    if g == "6":
        grup = ana[:2]
        if grup == "60":
            return "B" if ana in _GELIR_BORC else "A"
        if grup in ("64", "67"):
            return "A"
        if grup in ("61", "62", "63", "65", "66", "68"):
            return "B"
        if ana == "691":
            return "B"
        return None
    if g == "7":
        # 7/A: 7x0 gider (B), 7x1 yansıtma (A), 7x9 fark (kontrol dışı)
        son = ana[2]
        if son == "0":
            return "B"
        if son == "1":
            return "A"
        return None
    return None  # 8, 9 nazım hesaplar


# Ters bakiye için öneriler (ana hesap -> açıklama, önerilen karşı hesap)
TERS_ONERI = {
    "100": ("Kasa alacak bakiye veremez! Kayıt hatası / eksik tahsilat kaydı.", None),
    "101": ("Portföydeki çek alacak bakiye veremez; ciro/tahsil kayıtları kontrol edilmeli.", None),
    "102": ("Banka alacak bakiye: KMH/kredili hesap ise 300 Banka Kredileri'ne virman.", "300"),
    "120": ("Müşteri alacak bakiyede: avans ise 340 Alınan Sipariş Avansları'na virman "
            "(aynı firma 320'de varsa mahsup).", "340"),
    "121": ("Alacak senedi alacak bakiye veremez; tahsil/ciro kayıtları kontrol.", None),
    "131": ("Ortaktan alacak alacak bakiyede: 331 Ortaklara Borçlar'a virman.", "331"),
    "135": ("Personelden alacak alacak bakiyede: 335 Personele Borçlar'a virman.", "335"),
    "136": ("Diğer çeşitli alacak alacak bakiyede: 336 Diğer Çeşitli Borçlar'a virman.", "336"),
    "153": ("Ticari mal alacak bakiye: stok/maliyet kaydı hatalı (eksi stok).", None),
    "150": ("İlk madde alacak bakiye: stok/maliyet kaydı hatalı (eksi stok).", None),
    "159": ("Verilen avans alacak bakiyede: 320 Satıcılar'a virman.", "320"),
    "190": ("Devreden KDV alacak bakiye veremez; KDV mahsup kaydını kontrol et.", None),
    "191": ("İndirilecek KDV alacak bakiyede; KDV kayıtlarını kontrol et.", None),
    "195": ("İş avansı alacak bakiyede: 335/336'ya virman.", "335"),
    "196": ("Personel avansı alacak bakiyede: 335 Personele Borçlar'a virman.", "335"),
    "300": ("Banka kredisi borç bakiye: fazla ödeme / 102 ile karışmış olabilir.", "102"),
    "320": ("Satıcı borç bakiyede: avans ise 159 Verilen Sipariş Avansları'na virman "
            "(aynı firma 120'de varsa mahsup).", "159"),
    "331": ("Ortaklara borç borç bakiyede: 131 Ortaklardan Alacaklar'a virman.", "131"),
    "335": ("Personele borç borç bakiyede: 196 Personel Avansları / 135'e virman.", "196"),
    "336": ("Diğer çeşitli borç borç bakiyede: 136 Diğer Çeşitli Alacaklar'a virman.", "136"),
    "340": ("Alınan avans borç bakiyede: 120 Alıcılar'a virman.", "120"),
    "360": ("Ödenecek vergi borç bakiyede: fazla ödeme -> 193/136'ya virman veya tahakkuk eksik.", "193"),
    "361": ("Ödenecek SGK borç bakiyede: fazla ödeme / tahakkuk eksik.", "136"),
    "391": ("Hesaplanan KDV borç bakiye; KDV kayıtlarını kontrol et.", None),
}

# Aynı cari için virman/mahsup yapılacak hesap çiftleri:
# (borç çalışan hesap, alacak çalışan hesap, açıklama)
CARI_CIFTLER = [
    ("120", "320", "Aynı firma hem alıcı hem satıcı — 120/320 mahsubu"),
    ("120", "340", "Müşteriden alınan avans faturalanmış — 340/120 mahsubu"),
    ("159", "320", "Satıcıya verilen avans faturalanmış — 320/159 mahsubu"),
    ("131", "331", "Ortaktan alacak / ortağa borç — 331/131 mahsubu"),
    ("136", "336", "Diğer çeşitli alacak / borç — 336/136 mahsubu"),
    ("126", "326", "Verilen / alınan depozito aynı firmada — kontrol"),
    ("135", "335", "Personelden alacak / personele borç — 335/135 mahsubu"),
    ("196", "335", "Personel avansı / personele borç — 335/196 mahsubu"),
    ("121", "321", "Alacak senedi / borç senedi aynı firmada — kontrol"),
    ("120", "159", "Aynı firma 120 ve 159'da borç bakiyeli — kontrol"),
    ("320", "340", "Aynı firma 320 ve 340'ta alacak bakiyeli — kontrol"),
]

# Dönem sonu kapatılması gereken çiftler (ana hesap düzeyinde)
DONEM_SONU_CIFTLER = [
    ("191", "391", "KDV mahsubu: 391 Borç / 191 Alacak; kalan 190 Devreden KDV veya 360 Ödenecek KDV"),
    ("190", "391", "Devreden KDV + Hesaplanan KDV birlikte bakiyeli — KDV mahsubu yapılmamış"),
    ("193", "371", "Peşin ödenen vergiler 371'e / 370 ile mahsup edilmeli (yıl sonu)"),
    ("371", "370", "Dönem kârı peşin ödenen vergiler 370 ile mahsup edilmeli (yıl sonu)"),
]


# --------------------------------------------------------------------------- #
# Yardımcılar                                                                  #
# --------------------------------------------------------------------------- #

def tr_upper(s: str) -> str:
    return s.replace("i", "İ").replace("ı", "I").upper()


def _ascii_fold(s: str) -> str:
    s = tr_upper(s)
    tablo = str.maketrans("ÇĞİÖŞÜÂÎÛ", "CGIOSUAIU")
    s = s.translate(tablo)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


_UNVAN_EKLERI = [
    "LIMITED SIRKETI", "LTD STI", "LTD STI.", "LTD", "STI", "SIRKETI", "SIRK",
    "ANONIM SIRKETI", "A S", "AS", "SAN", "SANAYI", "TIC", "TICARET", "VE", "INS",
    "INSAAT", "NAK", "NAKLIYAT", "PAZ", "PAZARLAMA", "ITH", "IHR", "ITHALAT", "IHRACAT",
    "HIZ", "HIZMETLERI", "LTDSTI", "TAAH", "TAAHHUT", "MUH", "GIDA", "TURIZM", "OTO",
]


def cari_anahtar(ad: str) -> str:
    """Cari unvanını eşleştirme için normalize eder (unvan ekleri atılır)."""
    s = _ascii_fold(str(ad))
    s = re.sub(r"[^A-Z0-9 ]", " ", s)
    kelimeler = [k for k in s.split() if k]
    ekler = set(" ".join(_UNVAN_EKLERI).split())
    temiz = [k for k in kelimeler if k not in ekler]
    return " ".join(temiz or kelimeler)


def sayi(v) -> float:
    """'1.234,56' / '1,234.56' / 1234.56 / '(1.234,56)' -> float."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace("\xa0", "").replace(" ", "").replace("TL", "").replace("₺", "")
    if not s or s in ("-", "--"):
        return 0.0
    neg = s.startswith("(") and s.endswith(")")
    s = s.strip("()")
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        f = float(s)
    except ValueError:
        return 0.0
    return -f if neg else f


def fmt(x: float) -> str:
    return f"{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# --------------------------------------------------------------------------- #
# Mizan okuma                                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class Hesap:
    kod: str          # orijinal kod (ör. 120.01.005)
    ad: str
    borc: float
    alacak: float
    bakiye: float     # + borç, - alacak

    @property
    def ana(self) -> str:
        return re.sub(r"\D", "", self.kod)[:3]

    @property
    def yon(self) -> str:
        if self.bakiye > TOLERANS:
            return "B"
        if self.bakiye < -TOLERANS:
            return "A"
        return "0"


@dataclass
class Mizan:
    sirket: str
    dosya: Path
    sayfa: str
    hesaplar: list[Hesap] = field(default_factory=list)  # tüm satırlar
    yapraklar: list[Hesap] = field(default_factory=list)  # en alt seviye hesaplar


def _norm_baslik(v) -> str:
    return _ascii_fold(str(v)).replace(".", " ").strip()


def _kolon_bul(basliklar: list[str]) -> dict:
    """Başlık listesinden kolon indekslerini tespit eder."""
    k: dict = {}
    for i, b in enumerate(basliklar):
        if not b or b == "NAN":
            continue
        if "KOD" in b or b in ("HESAP NO", "HESAP NUMARASI", "NO"):
            k.setdefault("kod", i)
        elif ("HESAP ADI" in b or "ACIKLAMA" in b or b in ("AD", "ADI", "UNVAN", "HESAP ISMI", "ISIM")
              or "TANIM" in b):
            k.setdefault("ad", i)
        elif "BAKIYE" in b and "BORC" in b:
            k.setdefault("bborc", i)
        elif "BAKIYE" in b and "ALACAK" in b:
            k.setdefault("balacak", i)
        elif b in ("B/A", "B A", "BA", "DURUM", "BAKIYE TURU", "BAKIYE YONU", "YON"):
            k.setdefault("ba", i)
        elif "BAKIYE" in b:
            k.setdefault("bakiye", i)
        elif "BORC" in b and "DEVIR" not in b and "ACILIS" not in b:
            # Toplam borç tercih edilir ("TOPLAM BORC" > "BORC")
            if "TOPLAM" in b or "borc" not in k:
                k["borc"] = i
        elif "ALACAK" in b and "DEVIR" not in b and "ACILIS" not in b:
            if "TOPLAM" in b or "alacak" not in k:
                k["alacak"] = i
    # Sadece "HESAP" başlığı varsa kod kolonu say
    if "kod" not in k:
        for i, b in enumerate(basliklar):
            if b == "HESAP":
                k["kod"] = i
                break
    return k


_KOD_RE = re.compile(r"^\s*([1-9]\d{2})([\s.\-]?\d+)*\s*$")


def _sayfa_oku(df: pd.DataFrame) -> Optional[tuple[list[Hesap], dict]]:
    df = df.dropna(how="all").dropna(axis=1, how="all")
    if df.shape[0] < 3:
        return None
    baslik_idx, kolonlar = None, {}
    for idx in df.index[:40]:
        basliklar = [_norm_baslik(v) for v in df.loc[idx].values]
        k = _kolon_bul(basliklar)
        if ("borc" in k and "alacak" in k) or ("bborc" in k and "balacak" in k) or "bakiye" in k:
            baslik_idx, kolonlar = idx, k
            break
    if baslik_idx is None:
        return None

    veri = df.loc[baslik_idx:].iloc[1:]
    cols = list(df.columns)

    # Kod kolonu bulunamadıysa hesap kodu gibi görünen değerleri içeren kolonu seç
    if "kod" not in kolonlar:
        en_iyi, skor = None, 0
        for i, c in enumerate(cols):
            s = veri[c].astype(str).str.match(_KOD_RE).sum()
            if s > skor:
                en_iyi, skor = i, s
        if en_iyi is None:
            return None
        kolonlar["kod"] = en_iyi
    if "ad" not in kolonlar:
        # kod kolonundan sonraki ilk metin kolonu
        for i in range(kolonlar["kod"] + 1, len(cols)):
            if veri[cols[i]].astype(str).str.contains(r"[A-Za-zÇĞİÖŞÜçğıöşü]", regex=True).mean() > 0.5:
                kolonlar["ad"] = i
                break

    def al(row, key):
        i = kolonlar.get(key)
        return row.iloc[i] if i is not None else None

    hesaplar = []
    for _, row in veri.iterrows():
        kod_raw = al(row, "kod")
        if kod_raw is None or (isinstance(kod_raw, float) and pd.isna(kod_raw)):
            continue
        kod = str(kod_raw).strip()
        if re.fullmatch(r"\d+\.0", kod):  # excel sayısal kod 120.0
            kod = kod[:-2]
        ad = str(al(row, "ad") or "").strip()
        # Kod ve ad tek hücrede olabilir: "120.01.001 ABC LTD"
        m = re.match(r"^\s*([1-9]\d{2}(?:[\s.\-]?\d+)*)\s+(.+)$", kod)
        if m and not ad:
            kod, ad = m.group(1), m.group(2)
        if not _KOD_RE.match(kod):
            continue
        kod = re.sub(r"\s+", ".", kod.strip())
        borc, alacak = sayi(al(row, "borc")), sayi(al(row, "alacak"))
        if "bborc" in kolonlar or "balacak" in kolonlar:
            bakiye = sayi(al(row, "bborc")) - sayi(al(row, "balacak"))
        elif "bakiye" in kolonlar:
            bakiye = sayi(al(row, "bakiye"))
            ba = _norm_baslik(al(row, "ba") or "")
            if ba.startswith("A") or ba.startswith("(A"):
                bakiye = -abs(bakiye)
            elif ba.startswith("B"):
                bakiye = abs(bakiye)
            elif "borc" in kolonlar and "alacak" in kolonlar and abs(bakiye) > 0:
                # B/A kolonu yoksa işaret borç-alacak farkından gelir
                fark = borc - alacak
                if abs(fark) > TOLERANS:
                    bakiye = abs(bakiye) if fark > 0 else -abs(bakiye)
        else:
            bakiye = borc - alacak
        hesaplar.append(Hesap(kod=kod, ad=ad, borc=borc, alacak=alacak, bakiye=round(bakiye, 2)))
    if len(hesaplar) < 3:
        return None
    return hesaplar, kolonlar


def _yapraklari_bul(hesaplar: list[Hesap]) -> list[Hesap]:
    """Başka bir hesabın üst hesabı olmayan (en alt seviye) hesapları döner."""
    kodlar = [h.kod for h in hesaplar]
    ayracli = any(re.search(r"[.\-]", k) for k in kodlar)

    def ust_mu(a: str, b: str) -> bool:  # a, b'nin üst hesabı mı
        if a == b:
            return False
        if ayracli:
            return b.startswith(a + ".") or b.startswith(a + "-")
        return len(b) > len(a) and b.startswith(a)

    kod_set = sorted(set(kodlar))
    ust_kodlar = set()
    for i, a in enumerate(kod_set):
        for b in kod_set[i + 1:]:
            if not b.startswith(a):
                break
            if ust_mu(a, b):
                ust_kodlar.add(a)
                break
    gorulen, yaprak = set(), []
    for h in hesaplar:
        if h.kod in ust_kodlar or h.kod in gorulen:
            continue
        gorulen.add(h.kod)
        yaprak.append(h)
    return yaprak


def mizan_oku(dosya: Path, sirket: Optional[str] = None) -> Mizan:
    sirket = sirket or dosya.stem
    if dosya.suffix.lower() == ".csv":
        sayfalar = {"csv": pd.read_csv(dosya, header=None, sep=None, engine="python", dtype=str)}
    else:
        sayfalar = pd.read_excel(dosya, sheet_name=None, header=None, dtype=object)
    en_iyi = None
    for ad, df in sayfalar.items():
        sonuc = _sayfa_oku(df)
        if sonuc and (en_iyi is None or len(sonuc[0]) > len(en_iyi[1])):
            en_iyi = (ad, sonuc[0])
    if en_iyi is None:
        raise ValueError(f"{dosya.name}: mizan formatı tanınamadı (Borç/Alacak başlıkları bulunamadı)")
    m = Mizan(sirket=sirket, dosya=dosya, sayfa=str(en_iyi[0]), hesaplar=en_iyi[1])
    m.yapraklar = _yapraklari_bul(m.hesaplar)
    return m


# --------------------------------------------------------------------------- #
# Kontroller                                                                   #
# --------------------------------------------------------------------------- #

def ana_bakiyeler(m: Mizan) -> dict[str, float]:
    """Ana hesap bakiyeleri (yapraklardan toplanır, çift sayım olmaz)."""
    d: dict[str, float] = {}
    for h in m.yapraklar:
        d[h.ana] = d.get(h.ana, 0.0) + h.bakiye
    return {k: round(v, 2) for k, v in d.items()}


def denge_kontrol(m: Mizan) -> dict:
    tb = sum(h.borc for h in m.yapraklar)
    ta = sum(h.alacak for h in m.yapraklar)
    bakiye_toplam = sum(h.bakiye for h in m.yapraklar)
    return {
        "Toplam Borç": round(tb, 2),
        "Toplam Alacak": round(ta, 2),
        "Fark (Borç-Alacak)": round(tb - ta, 2),
        "Bakiyeler Toplamı": round(bakiye_toplam, 2),
        "Dengede": abs(tb - ta) < 1 and abs(bakiye_toplam) < 1,
    }


def ters_bakiyeler(m: Mizan) -> list[dict]:
    sonuc = []
    for h in m.yapraklar:
        beklenen = normal_yon(h.ana)
        if beklenen is None or h.yon == "0" or h.yon == beklenen:
            continue
        aciklama, karsi = TERS_ONERI.get(
            h.ana, (f"{h.ana} normalde {'borç' if beklenen == 'B' else 'alacak'} bakiye verir.", None)
        )
        oneri_kayit = ""
        if karsi:
            tutar = abs(h.bakiye)
            if h.yon == "A":  # alacak bakiyeyi kapatmak için hesabı borçlandır
                oneri_kayit = f"{h.kod} B / {karsi} A  {fmt(tutar)}"
            else:
                oneri_kayit = f"{karsi} B / {h.kod} A  {fmt(tutar)}"
        sonuc.append({
            "Hesap Kodu": h.kod,
            "Hesap Adı": h.ad,
            "Ana Hesap": f"{h.ana} {HESAP_ADLARI.get(h.ana, '')}".strip(),
            "Olması Gereken": "Borç" if beklenen == "B" else "Alacak",
            "Mevcut": "Borç" if h.yon == "B" else "Alacak",
            "Bakiye": abs(h.bakiye),
            "Açıklama": aciklama,
            "Önerilen Virman": oneri_kayit,
            "Kritik": h.ana in ("100", "101", "121", "150", "153", "190", "191", "391"),
        })
    sonuc.sort(key=lambda r: (not r["Kritik"], r["Hesap Kodu"]))
    return sonuc


def _cari_grupla(m: Mizan, ana: str) -> dict[str, list[Hesap]]:
    d: dict[str, list[Hesap]] = {}
    for h in m.yapraklar:
        if h.ana != ana or h.yon == "0" or not h.ad:
            continue
        # Alt hesabı olmayan ana hesabın kendisi cari değildir
        if re.sub(r"\D", "", h.kod) == ana:
            continue
        d.setdefault(cari_anahtar(h.ad), []).append(h)
    return d


def cari_virmanlar(m: Mizan, benzerlik: float = 0.92) -> list[dict]:
    sonuc = []
    gruplar_cache: dict[str, dict[str, list[Hesap]]] = {}

    def gr(ana):
        if ana not in gruplar_cache:
            gruplar_cache[ana] = _cari_grupla(m, ana)
        return gruplar_cache[ana]

    # 1) Hesap çiftleri arasında aynı cari
    for borc_h, alacak_h, aciklama in CARI_CIFTLER:
        g1, g2 = gr(borc_h), gr(alacak_h)
        if not g1 or not g2:
            continue
        anahtarlar2 = list(g2.keys())
        for anahtar, liste1 in g1.items():
            eslesme, tip = None, "Birebir"
            if anahtar in g2:
                eslesme = anahtar
            else:
                yakin = difflib.get_close_matches(anahtar, anahtarlar2, n=1, cutoff=benzerlik)
                if yakin:
                    eslesme, tip = yakin[0], "Benzer isim — kontrol et"
            if not eslesme:
                continue
            liste2 = g2[eslesme]
            b1 = sum(h.bakiye for h in liste1)
            b2 = sum(h.bakiye for h in liste2)
            # Mahsup anlamlı: biri borç diğeri alacak bakiyeli olmalı
            if b1 > TOLERANS and b2 < -TOLERANS:
                tutar = min(b1, -b2)
                net = b1 + b2
                kayit = (f"{', '.join(h.kod for h in liste2)} B / "
                         f"{', '.join(h.kod for h in liste1)} A  {fmt(tutar)}")
                kalan = (f"{borc_h}'de {fmt(net)} borç kalır" if net > TOLERANS else
                         f"{alacak_h}'de {fmt(-net)} alacak kalır" if net < -TOLERANS else "İkisi de sıfırlanır")
            elif borc_h + alacak_h in ("120159", "320340"):
                # Aynı yönlü çiftler: sadece bilgi amaçlı
                tutar, kayit, kalan = 0.0, "—", "Aynı yönlü bakiye; cari ilişki kontrol edilmeli"
                if not (abs(b1) > TOLERANS and abs(b2) > TOLERANS):
                    continue
            else:
                continue  # Ters bakiyeler zaten ters bakiye listesinde
            sonuc.append({
                "Tür": aciklama,
                "Cari": liste1[0].ad,
                "Eşleşme": tip if tip == "Birebir" else f"{tip}: '{liste2[0].ad}'",
                f"Hesap 1": ", ".join(h.kod for h in liste1),
                "Hesap 1 Bakiye": round(b1, 2),
                f"Hesap 2": ", ".join(h.kod for h in liste2),
                "Hesap 2 Bakiye": round(b2, 2),
                "Virman Tutarı": round(tutar, 2),
                "Önerilen Kayıt": kayit,
                "Virman Sonrası": kalan,
            })

    # 2) Aynı ana hesapta aynı cari birden fazla alt hesapta zıt bakiyeli
    ana_kodlar = sorted({h.ana for h in m.yapraklar if h.ana[0] in "123"})
    for ana in ana_kodlar:
        for anahtar, liste in gr(ana).items():
            borclular = [h for h in liste if h.yon == "B"]
            alacaklilar = [h for h in liste if h.yon == "A"]
            if not borclular or not alacaklilar:
                continue
            tb = sum(h.bakiye for h in borclular)
            ta = -sum(h.bakiye for h in alacaklilar)
            tutar = min(tb, ta)
            net = tb - ta
            sonuc.append({
                "Tür": f"Aynı cari {ana} içinde birden fazla alt hesapta zıt bakiye — iç virman",
                "Cari": liste[0].ad,
                "Eşleşme": "Birebir",
                "Hesap 1": ", ".join(h.kod for h in borclular),
                "Hesap 1 Bakiye": round(tb, 2),
                "Hesap 2": ", ".join(h.kod for h in alacaklilar),
                "Hesap 2 Bakiye": round(-ta, 2),
                "Virman Tutarı": round(tutar, 2),
                "Önerilen Kayıt": (f"{', '.join(h.kod for h in alacaklilar)} B / "
                                   f"{', '.join(h.kod for h in borclular)} A  {fmt(tutar)}"),
                "Virman Sonrası": (f"Tek hesapta {fmt(abs(net))} {'borç' if net > 0 else 'alacak'} kalır"
                                   if abs(net) > TOLERANS else "Sıfırlanır"),
            })
    sonuc.sort(key=lambda r: -r["Virman Tutarı"])
    return sonuc


def donem_sonu_kontrol(m: Mizan) -> list[dict]:
    ab = ana_bakiyeler(m)
    sonuc = []
    for h1, h2, aciklama in DONEM_SONU_CIFTLER:
        b1, b2 = ab.get(h1, 0.0), ab.get(h2, 0.0)
        if abs(b1) > TOLERANS and abs(b2) > TOLERANS:
            sonuc.append({
                "Hesaplar": f"{h1} {HESAP_ADLARI.get(h1, '')} / {h2} {HESAP_ADLARI.get(h2, '')}",
                f"Bakiye 1": b1,
                f"Bakiye 2": b2,
                "Açıklama": aciklama,
            })
    if abs(ab.get("190", 0)) > TOLERANS and abs(ab.get("360", 0)) > TOLERANS:
        # 360 içinde KDV alt hesabı varsa bilgi ver
        kdv360 = [h for h in m.yapraklar if h.ana == "360" and "KDV" in _ascii_fold(h.ad) and h.yon != "0"]
        if kdv360:
            sonuc.append({
                "Hesaplar": "190 Devreden KDV / 360 Ödenecek KDV",
                "Bakiye 1": ab["190"],
                "Bakiye 2": round(sum(h.bakiye for h in kdv360), 2),
                "Açıklama": "Aynı anda devreden KDV ve ödenecek KDV olamaz — mahsup kontrol edilmeli",
            })
    return sonuc


@dataclass
class KontrolSonucu:
    mizan: Mizan
    denge: dict
    ters: list[dict]
    cari: list[dict]
    donem: list[dict]


def kontrol_et(m: Mizan) -> KontrolSonucu:
    return KontrolSonucu(m, denge_kontrol(m), ters_bakiyeler(m), cari_virmanlar(m), donem_sonu_kontrol(m))


# --------------------------------------------------------------------------- #
# Dosya bulma                                                                  #
# --------------------------------------------------------------------------- #

UZANTILAR = {".xlsx", ".xls", ".xlsm", ".csv"}


def _en_guncel(dosyalar: list[Path]) -> Optional[Path]:
    dosyalar = [d for d in dosyalar if d.suffix.lower() in UZANTILAR and not d.name.startswith("~$")
                and not d.name.lower().startswith("mizan_kontrol_raporu")]
    if not dosyalar:
        return None
    mizanlar = [d for d in dosyalar if "mizan" in d.name.lower()]
    return max(mizanlar or dosyalar, key=lambda p: p.stat().st_mtime)


def mizan_dosyalari(klasor: Path) -> list[tuple[str, Path]]:
    """[(şirket, en güncel dosya)] listesi."""
    if klasor.is_file():
        return [(klasor.stem, klasor)]
    sonuc = []
    for alt in sorted(p for p in klasor.iterdir() if p.is_dir() and not p.name.startswith(".")):
        d = _en_guncel([p for p in alt.rglob("*") if p.is_file()])
        if d:
            sonuc.append((alt.name, d))
    for d in sorted(p for p in klasor.iterdir() if p.is_file()):
        if d.suffix.lower() in UZANTILAR and not d.name.startswith("~$") \
                and not d.name.lower().startswith("mizan_kontrol_raporu"):
            sonuc.append((d.stem, d))
    return sonuc


# --------------------------------------------------------------------------- #
# Raporlama                                                                    #
# --------------------------------------------------------------------------- #

def excel_rapor(sonuclar: list[KontrolSonucu], hatalar: list[tuple[str, str]], cikti: Path) -> Path:
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    ozet, ters_rows, cari_rows, donem_rows = [], [], [], []
    for s in sonuclar:
        ozet.append({
            "Şirket": s.mizan.sirket,
            "Dosya": s.mizan.dosya.name,
            "Dosya Tarihi": pd.Timestamp(s.mizan.dosya.stat().st_mtime, unit="s").strftime("%d.%m.%Y %H:%M"),
            "Hesap Sayısı": len(s.mizan.yapraklar),
            "Denge": "OK" if s.denge["Dengede"] else f"FARK {fmt(s.denge['Fark (Borç-Alacak)'])}",
            "Ters Bakiye": len(s.ters),
            "Kritik Ters": sum(1 for r in s.ters if r["Kritik"]),
            "Cari Virman": len(s.cari),
            "Toplam Virman Tutarı": round(sum(r["Virman Tutarı"] for r in s.cari), 2),
            "Dönem Sonu": len(s.donem),
        })
        ters_rows += [{"Şirket": s.mizan.sirket, **r, "Kritik": "EVET" if r["Kritik"] else ""} for r in s.ters]
        cari_rows += [{"Şirket": s.mizan.sirket, **r} for r in s.cari]
        donem_rows += [{"Şirket": s.mizan.sirket, **r} for r in s.donem]
    for sirket, hata in hatalar:
        ozet.append({"Şirket": sirket, "Dosya": hata})

    sayfalar = {
        "Özet": pd.DataFrame(ozet),
        "Ters Bakiye": pd.DataFrame(ters_rows),
        "Cari Virman": pd.DataFrame(cari_rows),
        "Dönem Sonu": pd.DataFrame(donem_rows),
    }
    baslik_fill = PatternFill("solid", fgColor="1F4E78")
    kritik_fill = PatternFill("solid", fgColor="F8CBAD")
    with pd.ExcelWriter(cikti, engine="openpyxl") as w:
        for ad, df in sayfalar.items():
            if df.empty:
                df = pd.DataFrame({"Sonuç": ["Bulgu yok ✓"]})
            df.to_excel(w, sheet_name=ad, index=False)
            ws = w.sheets[ad]
            ws.freeze_panes = "A2"
            ws.auto_filter.ref = ws.dimensions
            for c in ws[1]:
                c.font = Font(bold=True, color="FFFFFF")
                c.fill = baslik_fill
                c.alignment = Alignment(wrap_text=True, vertical="center")
            for i, col in enumerate(df.columns, 1):
                genislik = max([len(str(col))] + [len(str(v)) for v in df[col].head(500)]) + 2
                ws.column_dimensions[get_column_letter(i)].width = min(max(genislik, 10), 60)
                if pd.api.types.is_float_dtype(df[col]):
                    for cell in ws.iter_cols(min_col=i, max_col=i, min_row=2):
                        for c in cell:
                            c.number_format = "#,##0.00"
            if ad == "Ters Bakiye" and "Kritik" in df.columns:
                k = list(df.columns).index("Kritik") + 1
                for row in ws.iter_rows(min_row=2):
                    if row[k - 1].value == "EVET":
                        for c in row:
                            c.fill = kritik_fill
    return cikti


def metin_rapor(sonuclar: list[KontrolSonucu], hatalar: list[tuple[str, str]], max_satir: int = 8) -> str:
    """WhatsApp / konsol için kısa özet."""
    out = []
    for s in sonuclar:
        out.append(f"🏢 *{s.mizan.sirket}* — {s.mizan.dosya.name}")
        d = s.denge
        out.append("  ✅ Mizan dengede" if d["Dengede"]
                   else f"  ❌ Denge farkı: {fmt(d['Fark (Borç-Alacak)'])}")
        if s.ters:
            out.append(f"  ⚠️ Ters bakiye: {len(s.ters)} hesap")
            for r in s.ters[:max_satir]:
                isaret = "‼️" if r["Kritik"] else "•"
                out.append(f"    {isaret} {r['Hesap Kodu']} {r['Hesap Adı'][:30]}: "
                           f"{fmt(r['Bakiye'])} {r['Mevcut'].lower()}")
            if len(s.ters) > max_satir:
                out.append(f"    … +{len(s.ters) - max_satir} hesap (Excel raporunda)")
        if s.cari:
            out.append(f"  🔁 Virman gereken cari: {len(s.cari)}")
            for r in s.cari[:max_satir]:
                out.append(f"    • {r['Cari'][:30]}: {r['Hesap 1']} ↔ {r['Hesap 2']} "
                           f"→ {fmt(r['Virman Tutarı'])}")
            if len(s.cari) > max_satir:
                out.append(f"    … +{len(s.cari) - max_satir} cari (Excel raporunda)")
        for r in s.donem:
            out.append(f"  📅 {r['Hesaplar']}: {r['Açıklama']}")
        if not (s.ters or s.cari or s.donem) and d["Dengede"]:
            out.append("  ✓ Bulgu yok")
        out.append("")
    for sirket, hata in hatalar:
        out.append(f"❌ {sirket}: {hata}")
    return "\n".join(out).strip()


def calistir(hedef: str, cikti: Optional[str] = None) -> tuple[str, Optional[Path]]:
    yol = Path(hedef).expanduser()
    if not yol.exists():
        return f"⚠️ '{hedef}' bulunamadı.", None
    dosyalar = mizan_dosyalari(yol)
    if not dosyalar:
        return f"⚠️ '{hedef}' içinde mizan dosyası (.xlsx/.xls/.csv) bulunamadı.", None
    sonuclar, hatalar = [], []
    for sirket, dosya in dosyalar:
        try:
            sonuclar.append(kontrol_et(mizan_oku(dosya, sirket)))
        except Exception as exc:  # bir şirketin hatası diğerlerini durdurmasın
            hatalar.append((sirket, str(exc)))
    rapor_yolu = Path(cikti) if cikti else (
        (yol if yol.is_dir() else yol.parent)
        / f"mizan_kontrol_raporu_{pd.Timestamp.now():%Y%m%d_%H%M}.xlsx")
    excel_rapor(sonuclar, hatalar, rapor_yolu)
    return metin_rapor(sonuclar, hatalar), rapor_yolu


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="Mizan ters bakiye ve virman kontrolü")
    p.add_argument("hedef", nargs="?", default=os.environ.get("MIZAN_KLASOR", ""),
                   help="Mizan klasörü (alt klasör = şirket) veya tek mizan dosyası")
    p.add_argument("-o", "--cikti", help="Excel rapor yolu")
    a = p.parse_args(argv)
    if not a.hedef:
        p.error("Klasör belirtin veya MIZAN_KLASOR ortam değişkenini ayarlayın.")
    metin, rapor = calistir(a.hedef, a.cikti)
    print(metin)
    if rapor:
        print(f"\n📄 Excel rapor: {rapor}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
