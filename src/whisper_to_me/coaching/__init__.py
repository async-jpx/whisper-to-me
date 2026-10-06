"""Communication coaching: per-line and per-meeting reviews from the local
Ollama model, saved reviews, and the progress dashboard.

Self-contained on purpose. The daemon touches it only through `router()`, and
the web UI only through `webui/src/coaching/`, so removing the feature is
deleting both folders plus those two mount points.
"""

from .api import router

__all__ = ["router"]
