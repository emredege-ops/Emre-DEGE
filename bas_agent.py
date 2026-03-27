"""
Baş Agent — sadece agent oluşturma ve yönetiminden sorumludur.

Yetkiler:
  ✅ Agent başlat, listele, durdur, durumunu sorgula
  ❌ Dosya yazma / okuma yok
  ❌ Shell komutu çalıştırma yok

Alt agentlar da varsayılan olarak SADECE okuma yapabilir.
Herhangi bir değişiklik için kullanıcı onayı gerekir.
"""

import json
import threading
from pathlib import Path
from typing import Callable, Optional

from openai import OpenAI

from agent_manager import AgentManager


SYSTEM_PROMPT = """\
Sen Emre'nin kişisel AI asistanısın. WhatsApp üzerinden konuşuyorsunuz.

Görevin SADECE şunlar:
1. Emre'nin istediği agentları başlatmak.
2. Çalışan agentları listelemek, durumlarını raporlamak, durdurmak.

YASAK olanlar:
- Dosya oluşturma, silme, değiştirme
- Komut çalıştırma
- Herhangi bir sistem değişikliği

Bir agent oluştururken mutlaka şunu belirt:
"Bu agent SADECE okuma yapar, onayın olmadan hiçbir şeyi değiştirmez."

Kurallar:
- Türkçe yaz, kısa ve net ol.
- Teknik ayrıntıyı sadece sorulunca ver.
- Görev dışı istekler için: "Bu işlemi yapma yetkim yok, sadece agent yönetimi yapabilirim."
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
                    "description": "Agent'a verilecek görev. Otomatik olarak 'sadece oku, değiştirme' kısıtı eklenir.",
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
]

# Alt agentlara eklenen zorunlu güvenlik kısıtı
READONLY_SUFFIX = """

⚠️ GÜVENLİK KURALI: Bu agent SADECE okuma ve analiz yapabilir.
- Hiçbir dosyayı OLUŞTURMA, SILME veya DEĞIŞTIRME.
- Hiçbir komut ÇALIŞTIRMA (pip install dahil).
- Hiçbir sistem ayarını DEĞIŞTIRME.
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
        self.model = "llama3.2"
        self.manager = agent_manager
        self.send = whatsapp_sender
        self.memory_path = Path(memory_path)
        self._lock = threading.Lock()
        self._history: list[dict] = []
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

    # ------------------------------------------------------------------ #
    # Araç çalıştırıcı                                                     #
    # ------------------------------------------------------------------ #

    def _execute(self, name: str, inp: dict) -> str:
        try:
            if name == "agent_baslat":
                # Alt agenta güvenlik kısıtını otomatik ekle
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

        except Exception as exc:
            return f"Hata: {exc}"

        return f"Bilinmeyen araç: {name}"
