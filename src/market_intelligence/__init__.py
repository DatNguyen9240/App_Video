"""
Market Intelligence & Remote Integration Package for App_Video.

Provides:
  - Multi-version JSON normalization (Threads, TikTok, Shopee) without fabricating missing fields
  - Separate raw JSON archiving and normalized SQLite storage with deduplication
  - Authenticated Cloudflare Worker client (scoped Drive + D1 command queue)
  - Non-blocking background sync scheduler and Telegram remote command poller
"""

from .normalizer import normalize_payload, detect_platform, detect_schema_version, build_dedup_key
from .report_normalizer import normalize_muse_report, build_evidence_report_from_items, has_muse_report_sections
from .storage import MarketIntelligenceStore
from .cloudflare_client import CloudflareBridgeClient, mask_secret
from .sync_service import MarketSyncService
from .remote_control import RemoteCommandPoller, ALLOWED_COMMANDS

__all__ = [
    "normalize_payload",
    "detect_platform",
    "detect_schema_version",
    "build_dedup_key",
    "normalize_muse_report",
    "build_evidence_report_from_items",
    "has_muse_report_sections",
    "MarketIntelligenceStore",
    "CloudflareBridgeClient",
    "mask_secret",
    "MarketSyncService",
    "RemoteCommandPoller",
    "ALLOWED_COMMANDS",
]
