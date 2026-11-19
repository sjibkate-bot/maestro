"""Runtime settings, read from environment variables."""
import os

MONGO_URI = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.getenv("MONGO_DB", "supplychain")

# Claude is used for schema mapping, document extraction and Q&A.
# Credentials come from ANTHROPIC_API_KEY (or an `ant auth login` profile).
AI_MODEL = os.getenv("AI_MODEL", "claude-opus-5-5")
AI_ENABLED = os.getenv("AI_ENABLED", "true").lower() == "true"

# Max characters of unstructured text sent to the model per document.
MAX_DOC_CHARS = int(os.getenv("MAX_DOC_CHARS", "400000"))
