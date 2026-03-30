"""
Baş Agent — agent oluşturma/yönetimi + onaylı dosya/komut işlemleri.

Yetkiler:
  ✅ Agent başlat, listele, durdur, durumunu sorgula
  ✅ Dosya yaz — KULLANICI ONAYI gerekir (evet/hayır)
  ✅ Komut çalıştır — KULLANICI ONAYI gerekir (evet/hayır)

Alt agentlar SADECE okuma yapabilir.
Herhangi bir değişiklik için kullanıcı onayı gerekir.
"""

import json
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from openai import OpenAI

from agent_manager import AgentManager


SYSTEM_PROMPT = """\
Sen Emre'nin kişisel AI asistanısın. WhatsApp üzerinden konuşuyorsunuz.

Görevin:
1. Emre'nin istediği agentları başlatmak.
2. Çalışan agentları listelemek, durumlarını raporlamak, durdurmak.
3. Dosya oluşturmak/düzenlemek — ama ÖNCE onay istemek zorundasın.
4. Komut çalıştırmak — ama ÖNCE onay istemek zorundasın.

KURAL: dosya_yaz veya komut_calistir kullanmadan önce mutlaka
"[İşlem açıklaması] — onaylıyor musunuz? (evet/hayır)" diye sor.
Tool'u SADECE kullanıcı "evet" dedikten sonra çağır.

Alt agentlar ise HİÇBİR ZAMAN değişiklik yapamaz (sadece okuma).

Kurallar:
- Türkçe yaz, kısa ve net ol.
- Teknik ayrıntıyı sadece sorulunca ver.
"""

TOOLS = [
    {
        "name": "agent_baslat",
        "description": "Arka planda yeni bir agent başlatır. Agent SADECE okuma yapar, hiçbir şeyi değiştirmez.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Agent'a verilecek görev.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Opsiyonel özel isim (örn. 'analiz-1')",
                },
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "agent_listele",
        "description": "Tüm agentları (çalışan, biten, hatalı) listeler.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "agent_durum",
        "description": "Belirtilen agentin durumunu ve sonucunu döner.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"}
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "agent_durdur",
        "description": "Çalışan bir agenti durdurur.",
        "input_schema": {
            "type": "object",
            "properties": {
                "agent_id": {"type": "string"}
            },
            "required": ["agent_id"],
        },
    },
    {
        "name": "dosya_yaz",
        "description": (
            "Bir dosyayı diske yazar veya üzerine yazar. "
            "SADECE kullanıcı 'evet' onayı verdikten sonra çağır."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "yol": {
                    "type": "string",
                    "description": "Dosya yolu (örn. 'scraper.py')",
                },
                "icerik": {
                    "type": "string",
                    "description": "Dosyaya yazılacak içerik",
                },
            },
            "required": ["yol", "icerik"],
        },
    },
    {
        "name": "komut_calistir",
        "description": (
            "Bir kabuk komutu çalıştırır. "
            "SADECE kullanıcı 'evet' onayı verdikten sonra çağır."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "komut": {
                    "type": "string",
                    "description": "Çalıştırılacak komut (örn. 'pip install requests')",
                },
            },
            "required": ["komut"],
        },
    },
]

# Alt agentlara eklenen zorunlu güvenlik kısıtı
READONLY_SUFFIX = """

⚠️ GÜVENLİK KURALI: Bu agent SADECE okuma ve analiz yapabilir.
- Hiçbir dosyayı OLUŞTURMA, SİLME veya DEĞİŞTİRME.
- Hiçbir komut ÇALIŞTIRMA (pip install dahil).
- Hiçbir sistem ayarını DEĞİŞTİRME.
- Bulduklarını RAPORLA, değiştirme.
Kullanılabilir araçlar: Read, Glob, Grep (sadece okuma)."""


class BasAgent:
    def __init__(
        self,
        agent_manager: AgentManager,
        whatsapp_sender: Optional[Callable] = None,
        memory_path: str = "memory.json",
    ):
        self.client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama",
        )
        self.model = "llama3.1:8b"
        self.manager = agent_manager
        self.send = whatsapp_sender
        self.memory_path = Path(memory_path)
        self._lock = threading.Lock()
        self._history: list[dict] = []
        # Onay bekleyen işlem: {"tool": ..., "inp": ..., "desc": ...}
        self._pending: Optional[dict] = None
        self._load_memory()

    # ------------------------------------------------------------------ #
    # Hafıza                                                               #
    # ------------------------------------------------------------------ #

    def _load_memory(self) -> None:
        if self.memory_path.exists():
            try:
                data = json.loads(self.memory_path.read_text(encoding="utf-8"))
                self._history = data.get("history", [])
            except Exception:
                self._history = []

    def _save_memory(self) -> None:
        data = {"history": self._history[-50:]}
        self.memory_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    # ------------------------------------------------------------------ #
    # Ana giriş noktası                                                    #
    # ------------------------------------------------------------------ #

    def process_message(self, user_message: str) -> str:
        with self._lock:
            lower = user_message.strip().lower()

            # Onay bekleyen işlem varsa evet/hayır kontrolü yap
            if self._pending is not None:
                if lower in ("evet", "e", "yes", "y"):
                    pending = self._pending
                    self._pending = None
                    result = self._execute(pending["tool"], pending["inp"])
                    reply = f"✅ Yapıldı.\n{result}"
                    self._history.append({"role": "user", "content": user_message})
                    self._history.append({"role": "assistant", "content": reply})
                    self._save_memory()
                    return reply
                elif lower in ("hayır", "hayir", "h", "no", "n"):
                    self._pending = None
                    reply = "❌ İptal edildi."
                    self._history.append({"role": "user", "content": user_message})
                    self._history.append({"role": "assistant", "content": reply})
                    self._save_memory()
                    return reply
                # "evet/hayır" değilse normal konuşmaya devam et, pending'i temizle
                self._pending = None

            self._history.append({"role": "user", "content": user_message})
            messages = list(self._history)

        response_text = self._run_loop(messages)

        with self._lock:
            self._history.append({"role": "assistant", "content": response_text})
            self._save_memory()

        return response_text

    # ------------------------------------------------------------------ #
    # Tool-use döngüsü                                                     #
    # ------------------------------------------------------------------ #

    def _run_loop(self, messages: list) -> str:
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
        tools_openai = [{"type": "function", "function": t} for t in TOOLS]

        while True:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=tools_openai,
            )

            msg = response.choices[0].message
            finish = response.choices[0].finish_reason

            if finish == "stop" or not msg.tool_calls:
                return msg.content or ""

            full_messages.append(msg)

            for tc in msg.tool_calls:
                inp = json.loads(tc.function.arguments)
                result = self._execute(tc.function.name, inp)
                full_messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

                # Onay bekleniyor sinyali — döngüyü kır ve kullanıcıya ilet
                if result.startswith("__ONAY_BEKLE__:"):
                    return result[len("__ONAY_BEKLE__:"):]

    # ------------------------------------------------------------------ #
    # Araç çalıştırıcı                                                     #
    # ------------------------------------------------------------------ #

    def _execute(self, name: str, inp: dict) -> str:
        try:
            if name == "agent_baslat":
                guvenli_prompt = inp["prompt"] + READONLY_SUFFIX
                agent_id = self.manager.start_agent(
                    prompt=guvenli_prompt,
                    agent_id=inp.get("agent_id"),
                )
                return f"Başlatıldı: {agent_id} (sadece okuma modunda)"

            elif name == "agent_listele":
                agents = self.manager.list_agents()
                if not agents:
                    return "Hiç agent yok."
                lines = []
                for a in agents:
                    ts = a.created_at.strftime("%H:%M")
                    lines.append(f"• {a.agent_id} [{a.status}] {ts} — {a.prompt[:60]}")
                return "\n".join(lines)

            elif name == "agent_durum":
                a = self.manager.get_agent(inp["agent_id"])
                if not a:
                    return f"Bulunamadı: {inp['agent_id']}"
                out = f"Durum: {a.status}\n"
                if a.result:
                    out += f"Sonuç:\n{a.result[:800]}"
                if a.error:
                    out += f"Hata: {a.error}"
                return out

            elif name == "agent_durdur":
                ok = self.manager.stop_agent(inp["agent_id"])
                return "Durduruldu." if ok else f"Bulunamadı: {inp['agent_id']}"

            elif name == "dosya_yaz":
                yol = inp["yol"]
                icerik = inp["icerik"]
                desc = f"*{yol}* dosyası yazılacak ({len(icerik)} karakter)"
                with self._lock:
                    self._pending = {"tool": "_dosya_yaz_execute", "inp": inp, "desc": desc}
                return f"__ONAY_BEKLE__:📝 {desc}\nOnaylıyor musunuz? (evet/hayır)"

            elif name == "komut_calistir":
                komut = inp["komut"]
                desc = f"Komut çalıştırılacak: `{komut}`"
                with self._lock:
                    self._pending = {"tool": "_komut_execute", "inp": inp, "desc": desc}
                return f"__ONAY_BEKLE__:⚙️ {desc}\nOnaylıyor musunuz? (evet/hayır)"

            elif name == "_dosya_yaz_execute":
                path = Path(inp["yol"])
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(inp["icerik"], encoding="utf-8")
                return f"`{inp['yol']}` yazıldı."

            elif name == "_komut_execute":
                proc = subprocess.run(
                    inp["komut"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                out = proc.stdout.strip()
                err = proc.stderr.strip()
                parts = []
                if out:
                    parts.append(f"Çıktı:\n{out[:600]}")
                if err:
                    parts.append(f"Hata:\n{err[:400]}")
                return "\n".join(parts) if parts else "Komut tamamlandı (çıktı yok)."

        except Exception as exc:
            return f"Hata: {exc}"

        return f"Bilinmeyen araç: {name}"
