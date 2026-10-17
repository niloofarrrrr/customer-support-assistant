import os

from dotenv import load_dotenv

load_dotenv()

AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini").strip().lower()

GEMINI_MODEL = os.getenv("GEMINI_MODEL", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "").strip()