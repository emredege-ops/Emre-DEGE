"""
Basit Claude Agent - Claude Agent SDK ile hazırlanmıştır.

Kullanım:
    python agent.py
    python agent.py "Bana Python'da bir merhaba dünya yaz"

Gereksinimler:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY="your-api-key"
"""

import sys
import anyio
from claude_agent_sdk import query, ClaudeAgentOptions, ResultMessage, AssistantMessage, TextBlock


async def run_agent(prompt: str) -> None:
    print(f"Kullanıcı: {prompt}")
    print("Agent: ", end="", flush=True)

    async for message in query(
        prompt=prompt,
        options=ClaudeAgentOptions(
            allowed_tools=["Read", "Glob", "Grep", "Bash"],
        ),
    ):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, TextBlock):
                    print(block.text, end="", flush=True)
        elif isinstance(message, ResultMessage):
            if not message.result:
                continue
            print()
            print(f"\n[Bitti | Stop reason: {message.stop_reason}]")
            break

    print()


if __name__ == "__main__":
    default_prompt = "Merhaba! Python'da basit bir hesap makinesi örneği yazar mısın?"
    prompt = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else default_prompt
    anyio.run(run_agent, prompt)
