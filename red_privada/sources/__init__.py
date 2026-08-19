from red_privada.sources.base import BaseCollector, build_collectors
from red_privada.sources.gob_presidency import GobPresidencyCollector
from red_privada.sources.rss_feed import RSSFeedCollector
from red_privada.sources.web_column import WebColumnCollector

__all__ = [
    "BaseCollector",
    "GobPresidencyCollector",
    "RSSFeedCollector",
    "WebColumnCollector",
    "build_collectors",
]
