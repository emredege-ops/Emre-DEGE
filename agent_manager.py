"""
Agent Manager — arka planda çalışan agentları yönetir.

Her agent bağımsız bir asyncio task olarak çalışır.
Tamamlandığında notify_callback tetiklenir (WhatsApp bildirimi için).
"""

import asyncio
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ResultMessage,
    TextBlock,
    query,
)


@dataclass
class AgentInfo:
    agent_id: str
    prompt: str
    status: str          # "running" | "completed" | "failed" | "stopped"
    created_at: datetime
    result: Optional[str] = None
    error: Optional[str] = None
    logs: list = field(default_factory=list)


class AgentManager:
    """Thread-safe agent havuzu.

    Flask (sync) tarafından çağrılır, agentlar ayrı bir asyncio
    event loop'unda çalışır.
    """

    def __init__(self, notify_callback: Optional[Callable] = None):
        self._agents: dict[str, AgentInfo] = {}
        self._lock = threading.Lock()
        self._notify = notify_callback

        # Kalıcı arka plan asyncio döngüsü
        self._loop = asyncio.new_event_loop()
        t = threading.Thread(target=self._loop.run_forever, daemon=True)
        t.start()

    # ------------------------------------------------------------------ #
    # Genel API                                                            #
    # ------------------------------------------------------------------ #

    def start_agent(self, prompt: str, agent_id: Optional[str] = None) -> str:
        with self._lock:
            if agent_id is None:
                agent_id = f"agent-{len(self._agents) + 1}"
            # İsim çakışmasını önle
            base, idx = agent_id, 2
            while agent_id in self._agents:
                agent_id = f"{base}-{idx}"
                idx += 1

            self._agents[agent_id] = AgentInfo(
                agent_id=agent_id,
                prompt=prompt,
                status="running",
                created_at=datetime.now(),
            )

        asyncio.run_coroutine_threadsafe(
            self._run(agent_id, prompt), self._loop
        )
        return agent_id

    def stop_agent(self, agent_id: str) -> bool:
        with self._lock:
            if agent_id not in self._agents:
                return False
            self._agents[agent_id].status = "stopped"
            return True

    def list_agents(self) -> list[AgentInfo]:
        with self._lock:
            return list(self._agents.values())

    def get_agent(self, agent_id: str) -> Optional[AgentInfo]:
        with self._lock:
            return self._agents.get(agent_id)

    # ------------------------------------------------------------------ #
    # İç çalışma                                                           #
    # ------------------------------------------------------------------ #

    async def _run(self, agent_id: str, prompt: str) -> None:
        result_parts: list[str] = []

        try:
            async for message in query(
                prompt=prompt,
                options=ClaudeAgentOptions(
                    allowed_tools=["Read", "Glob", "Grep", "Bash", "Write", "Edit"],
                ),
            ):
                # "stopped" işaretlendiyse sonucu yoksay
                with self._lock:
                    if self._agents[agent_id].status == "stopped":
                        return

                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if isinstance(block, TextBlock) and block.text:
                            with self._lock:
                                self._agents[agent_id].logs.append(block.text)
                            result_parts.append(block.text)

                elif isinstance(message, ResultMessage):
                    with self._lock:
                        self._agents[agent_id].status = "completed"
                        self._agents[agent_id].result = "\n".join(result_parts)
                    break

        except Exception as exc:
            with self._lock:
                self._agents[agent_id].status = "failed"
                self._agents[agent_id].error = str(exc)

        # Bildirim gönder
        if self._notify:
            with self._lock:
                info = self._agents[agent_id]
            try:
                self._notify(agent_id, info)
            except Exception:
                pass
