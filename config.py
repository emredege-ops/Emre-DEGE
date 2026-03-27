import os
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY    = os.environ["ANTHROPIC_API_KEY"]
TWILIO_ACCOUNT_SID   = os.environ["TWILIO_ACCOUNT_SID"]
TWILIO_AUTH_TOKEN    = os.environ["TWILIO_AUTH_TOKEN"]
TWILIO_WHATSAPP_FROM = os.environ["TWILIO_WHATSAPP_FROM"]
OWNER_WHATSAPP       = os.environ["OWNER_WHATSAPP"]
