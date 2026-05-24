import os
from typing import Dict, List

from app.utils.logger import get_logger

logger = get_logger(__name__)


class VideoFetcherService:
    """Fetches stock/raw video assets based on topic keywords."""

    def __init__(self) -> None:
        self.provider = os.getenv("VIDEO_PROVIDER", "pexels")

    def fetch(self, topic: str, limit: int = 3) -> Dict[str, List[str]]:
        """
        Retrieve source footage URLs.

        Replace placeholder URLs with real provider API requests.
        """
        logger.info("Fetching videos with provider=%s, topic=%s", self.provider, topic)

        assets = [f"https://example.com/{topic.replace(' ', '-').lower()}-{i}.mp4" for i in range(1, limit + 1)]

        return {
            "provider": self.provider,
            "assets": assets,
        }
