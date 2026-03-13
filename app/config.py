import os
import logging
from dotenv import load_dotenv

load_dotenv()

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

VERTEX_PROJECT_ID: str = os.getenv("VERTEX_PROJECT_ID", "")
VERTEX_LOCATION: str = os.getenv("VERTEX_LOCATION", "us-central1")
VERTEX_MODEL: str = os.getenv("VERTEX_MODEL", "gemini-2.5-flash-lite")
GOOGLE_APPLICATION_CREDENTIALS: str = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

ALLOWED_ORIGINS: list[str] = os.getenv("ALLOWED_ORIGINS", "*").split(",")
CACHE_TTL_SECONDS: int = int(os.getenv("CACHE_TTL_SECONDS", "3600"))
MAX_HISTORY_MESSAGES: int = int(os.getenv("MAX_HISTORY_MESSAGES", "6"))
MAX_CONTENT_LENGTH: int = int(os.getenv("MAX_CONTENT_LENGTH", "3000"))
RATE_LIMIT_PER_MINUTE: int = int(os.getenv("RATE_LIMIT_PER_MINUTE", "10"))

KNOWLEDGE_BASE_PATH: str = os.path.join(
    os.path.dirname(__file__), "data", "knowledge.md"
)

KNOWLEDGE_DB_PATH: str = os.getenv(
    "KNOWLEDGE_DB_PATH",
    os.path.join(os.path.dirname(__file__), "data", "knowledge.db"),
)
