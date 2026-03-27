import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY    = os.environ.get("ANTHROPIC_API_KEY", "")  # Ollama ile opsiyonel
TWILIO_ACCOUNT_SID   = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN    = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]
OWNER_WHATSAPP       = os.environ["OWNER_WHATSAPP"]
