"""
Baş Agent — WhatsApp mesajlarını anlayıp agentları yöneten orkestratör.

Hafıza: memory.json dosyasında son 50 konuşma turu saklanır.
Araçlar: agent yönetimi, dosya okuma/yazma, shell komutu.
"""

import json
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from openai import OpenAI  # Ollama, OpenAI-uyumlu API sunar

from agent_manager import AgentManager


SYSTEM_PROMPT = """\
Sen Emre'nin kişisel AI asistanısın. WhatsApp üzerinden konuşuyorsunuz.

Görevlerin:
1. Emre'nin istediği agentları kurup başlatmak.
2. Çalışan agentları izlemek, raporlamak, durdurmak.
3. Yeni agent için Python kodu yazmak, kaydetmek, pip ile kurmak.
4. Hataları teşhis edip düzeltmek.

Kurallar:
- Türkçe yaz, kısa ve net ol — bu bir WhatsApp sohbeti.
- Teknik ayrıntıyı sadece sorulunca ver.
- Araç çağırırken sessizce çalış, sonucu özetle anlat.
- Bir görevi tamamlamadan önce gerekli bilgi eksikse kısa soru sor.
"""

TOOLS = [
    {
        "name": "agent_baslat",
        "description": "Arka planda yeni bir agent başlatır ve agent_id döner.",
        "input_schema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "Agent'a verilecek tam görev açıklaması",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Opsiyonel özel isim (örn. 'scraper-1')",
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
        "name": "dosya_oku",
        "description": "Verilen yoldaki dosyanın içeriğini okur.",
        "input_schema": {
            "type": "object",
            "properties": {
                "yol": {"type": "string"}
            },
            "required": ["yol"],
        },
    },
    {
        "name": "dosya_yaz",
        "description": "Dosya oluşturur veya üzerine yazar.",
        "input_schema": {
            "type": "object",
            "properties": {
                "yol": {"type": "string"},
                "icerik": {"type": "string"},
            },
            "required": ["yol", "icerik"],
        },
    },
    {
        "name": "komut_calistir",
        "description": (
            "Shell komutu çalıştırır. "
            "pip install, python script.py, ls vb. için kullan."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "komut": {"type": "string"}
            },
            "required": ["komut"],
        },
    },
]


class BasAgent:
    def __init__(
        self,
        agent_manager: AgentManager,
        whatsapp_sender: Optional[Callable] = None,
        memory_path: str = "memory.json",
    ):
        # Ollama, localhost:11434 üzerinde OpenAI-uyumlu API sunar
        self.client = OpenAI(
            base_url="http://localhost:11434/v1",
            api_key="ollama",  # Ollama için şart değil ama zorunlu alan
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
        # Sistem mesajını başa ekle
        full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

        # TOOLS'u OpenAI formatına çevir
        tools_openai = [
            {"type": "function", "function": t} for t in TOOLS
        ]

        while True:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=full_messages,
                tools=tools_openai,
            )

            msg = response.choices[0].message
            finish = response.choices[0].finish_reason

            # Cevap metin ise bitir
            if finish == "stop" or not msg.tool_calls:
                return msg.content or ""

            # Tool çağrısı varsa çalıştır
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
                agent_id = self.manager.start_agent(
                    prompt=inp["prompt"],
                    agent_id=inp.get("agent_id"),
                )
                return f"Başlatıldı: {agent_id}"

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

            elif name == "dosya_oku":
                return Path(inp["yol"]).read_text(encoding="utf-8")

            elif name == "dosya_yaz":
                p = Path(inp["yol"])
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(inp["icerik"], encoding="utf-8")
                return f"Yazıldı: {inp['yol']}"

            elif name == "komut_calistir":
                r = subprocess.run(
                    inp["komut"],
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                out = (r.stdout + r.stderr).strip()
                return out[:2000] if out else "Tamamlandı (çıktı yok)."

        except subprocess.TimeoutExpired:
            return "Timeout — komut 60 saniyede tamamlanamadı."
        except Exception as exc:
            return f"Hata: {exc}"

        return f"Bilinmeyen araç: {name}"
