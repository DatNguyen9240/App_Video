"""
Test Suite 13: Market Intelligence Normalization, Storage & Deduplication
==========================================================================
Verifies against REAL Muse AI Drive fixtures (POC 001, POC 002, Pilot 001)
plus TikTok & Shopee multi-version JSON variants:
  - Multi-version schema parsing without fabricating missing fields
  - Accurate platform detection (including @shopee_vn on threads.com)
  - Separate raw JSON file archiving and normalized SQLite storage
  - Strict deduplication across files and simulated app restarts
  - Filtering by platform, content_group, purchase_intent, and keyword search
"""

import json
import os
import sys
import tempfile
import unittest

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(ROOT_DIR, "src")
FIXTURES_DIR = os.path.join(ROOT_DIR, "tests", "fixtures", "market")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from market_intelligence.normalizer import (
    detect_platform,
    detect_schema_version,
    normalize_payload,
)
from market_intelligence.storage import MarketIntelligenceStore


class TestMarketNormalizerAndStorage(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.store = MarketIntelligenceStore(data_dir=self.tmp_dir.name)

    def tearDown(self):
        self.store.close()
        self.tmp_dir.cleanup()

    def _load_fixture_bytes(self, filename: str) -> bytes:
        path = os.path.join(FIXTURES_DIR, filename)
        with open(path, "rb") as f:
            return f.read()

    def test_real_fixtures_exist_and_normalize_without_fabrication(self):
        """Real POC 001, POC 002, and Pilot 001 JSONs normalize faithfully without inventing missing fields."""
        poc1_obj = json.loads(self._load_fixture_bytes("threads_poc_001.json").decode("utf-8"))
        poc2_obj = json.loads(self._load_fixture_bytes("threads_crawl_poc_002.json").decode("utf-8"))
        pilot1_obj = json.loads(self._load_fixture_bytes("threads_crawl_pilot_001.json").decode("utf-8"))

        n1 = normalize_payload(poc1_obj, file_name="threads_poc_001.json")
        n2 = normalize_payload(poc2_obj, file_name="threads_crawl_poc_002.json")
        n3 = normalize_payload(pilot1_obj, file_name="threads_crawl_pilot_001.json")

        self.assertEqual(n1["schema_version"], "muse_threads_poc_001")
        self.assertEqual(n2["schema_version"], "muse_crawl_v1")
        self.assertEqual(n3["schema_version"], "muse_crawl_v2")

        self.assertEqual(len(n1["items"]), 36)
        self.assertEqual(len(n2["items"]), 10)
        self.assertEqual(len(n3["items"]), 50)

        # @shopee_vn on threads.com MUST remain 'threads', never misclassified as 'shopee'
        first_poc1 = n1["items"][0]
        self.assertEqual(first_poc1["post_id"], "DgKoO8QjrzA")
        self.assertEqual(first_poc1["platform"], "threads")
        self.assertIsNone(first_poc1["content_group"])
        self.assertIsNone(first_poc1["purchase_intent"])
        self.assertIn("search_groups", first_poc1["extra_meta"])

        # POC 002 has neither content_group nor purchase_intent -> must stay None
        for item in n2["items"]:
            self.assertEqual(item["platform"], "threads")
            self.assertIsNone(item["content_group"])
            self.assertIsNone(item["purchase_intent"])

        # Pilot 001 preserves exact content_group & purchase_intent values
        first_pilot = n3["items"][0]
        self.assertEqual(first_pilot["platform"], "threads")
        self.assertEqual(first_pilot["content_group"], "deal_voucher")
        self.assertEqual(first_pilot["purchase_intent"], "trung_binh")

    def test_tiktok_and_shopee_variants_and_dedup_keys(self):
        """Supports TikTok and Shopee JSON schemas with platform-appropriate dedup keys and null missing fields."""
        tiktok_payload = {
            "version": "tiktok_v2",
            "items": [
                {
                    "video_id": "741234567890",
                    "author": "@review_cong_nghe",
                    "caption": "Review tai nghe giá rẻ dưới 300k",
                    "url": "https://www.tiktok.com/@review_cong_nghe/video/741234567890",
                    "views": 125000,
                    "likes": 4200,
                    "content_group": "review_so_sanh",
                    "purchase_intent": "cao",
                }
            ],
        }
        shopee_payload = [
            {
                "platform": "shopee",
                "shop_id": "889900",
                "item_id": "1122334455",
                "product_name": "Tai nghe Bluetooth chống ồn",
                "price": 259000,
                "historical_sold": 1400,
                "rating": 4.9,
                "url": "https://shopee.vn/product/889900/1122334455",
            }
        ]

        r_tt = self.store.ingest_raw_file(
            file_id="tt_file_1",
            file_name="tiktok_daily.json",
            raw_bytes=json.dumps(tiktok_payload).encode("utf-8"),
        )
        r_sp = self.store.ingest_raw_file(
            file_id="sp_file_1",
            file_name="shopee_deals.json",
            raw_bytes=json.dumps(shopee_payload).encode("utf-8"),
        )

        self.assertEqual(r_tt["inserted"], 1)
        self.assertEqual(r_sp["inserted"], 1)

        tt_items = self.store.query_items(platform="tiktok")["items"]
        self.assertEqual(len(tt_items), 1)
        self.assertEqual(tt_items[0]["dedup_key"], "tiktok:item:741234567890")
        self.assertEqual(tt_items[0]["views_count"], 125000)
        self.assertIsNone(tt_items[0]["price"])

        sp_items = self.store.query_items(platform="shopee")["items"]
        self.assertEqual(len(sp_items), 1)
        self.assertEqual(sp_items[0]["dedup_key"], "shopee:item:889900:1122334455")
        self.assertEqual(sp_items[0]["price"], 259000.0)
        self.assertIsNone(sp_items[0]["content_group"])
        self.assertIsNone(sp_items[0]["purchase_intent"])

    def test_raw_and_normalized_separation_plus_restart_dedup(self):
        """Ingesting real files across restarts archives raw files separately and creates zero duplicates."""
        b1 = self._load_fixture_bytes("threads_poc_001.json")
        b2 = self._load_fixture_bytes("threads_crawl_poc_002.json")
        b3 = self._load_fixture_bytes("threads_crawl_pilot_001.json")

        res1 = self.store.ingest_raw_file("drv_poc1", "threads_poc_001.json", b1, modified_time="2026-11-25T22:00:00Z")
        res2 = self.store.ingest_raw_file("drv_poc2", "threads_crawl_poc_002.json", b2, modified_time="2026-11-26T03:15:20Z")
        res3 = self.store.ingest_raw_file("drv_pilot1", "threads_crawl_pilot_001.json", b3, modified_time="2026-11-26T05:31:33Z")

        total_parsed = res1["parsed"] + res2["parsed"] + res3["parsed"]
        self.assertEqual(total_parsed, 96)

        stats_before_restart = self.store.get_stats()
        count_before = stats_before_restart["total_items"]
        self.assertGreater(count_before, 0)
        self.assertLessEqual(count_before, 96)
        self.assertEqual(stats_before_restart["total_raw_files"], 3)

        # Verify raw files are preserved verbatim on disk
        raw_files = self.store.list_raw_files()
        self.assertEqual(len(raw_files), 3)
        for rf in raw_files:
            self.assertTrue(os.path.exists(rf["raw_path"]))

        # Simulate App_Video shutdown & restart with same data_dir
        self.store.close()
        reopened_store = MarketIntelligenceStore(data_dir=self.tmp_dir.name)
        try:
            # Re-ingest same files (as if restart forced re-read) -> ZERO new inserts
            re1 = reopened_store.ingest_raw_file("drv_poc1", "threads_poc_001.json", b1, modified_time="2026-11-25T22:00:00Z")
            re2 = reopened_store.ingest_raw_file("drv_poc2", "threads_crawl_poc_002.json", b2, modified_time="2026-11-26T03:15:20Z")
            re3 = reopened_store.ingest_raw_file("drv_pilot1", "threads_crawl_pilot_001.json", b3, modified_time="2026-11-26T05:31:33Z")

            self.assertEqual(re1["inserted"] + re2["inserted"] + re3["inserted"], 0)
            self.assertEqual(reopened_store.get_stats()["total_items"], count_before)

            # Filtering & search verification
            deal_high = reopened_store.query_items(
                platform="threads",
                content_group="deal_voucher",
                purchase_intent="trung_binh",
                search="Shopee",
            )
            self.assertGreater(deal_high["total"], 0)
            first_id = deal_high["items"][0]["id"]
            detail = reopened_store.get_item_detail(first_id)
            self.assertIsNotNone(detail)
            self.assertIn("raw_payload", detail)
            self.assertIn("post_id", detail["raw_payload"])

            # Verify 5-section evidence report & Muse report normalization
            ev_report = reopened_store.build_evidence_report(platform="all")
            self.assertIn("trending_highlights", ev_report)
            self.assertIn("consumer_needs_problems", ev_report)
            self.assertIn("affiliate_opportunities", ev_report)
            self.assertIn("content_suggestions", ev_report)
            self.assertIn("data_quality", ev_report)
            self.assertIn("source_links", ev_report)
            self.assertGreater(len(ev_report["trending_highlights"]), 0)
            self.assertGreater(len(ev_report["affiliate_opportunities"]), 0)
            self.assertGreater(len(ev_report["source_links"]), 0)
            self.assertEqual(ev_report["data_quality"]["total_items"], count_before)

            # Stored Muse signals report from POC 001 is also persisted
            saved_reports = reopened_store.list_reports(limit=5)
            self.assertGreaterEqual(len(saved_reports), 1)
        finally:
            self.store = reopened_store

    def test_explicit_muse_analysis_report_normalization(self):
        """Explicit Muse 5-section report normalizes cleanly while tracking missing sections honestly."""
        from market_intelligence.report_normalizer import normalize_muse_report

        muse_report_json = {
            "run_id": "muse-report-20261126",
            "platform": "threads",
            "report": {
                "title": "Vietnam Beauty & Shopee Deals Weekly",
                "trending_highlights": [{"title": "Săn voucher Shopee Live 30%", "count": 14}],
                "consumer_needs_problems": [{"title": "Da nhạy cảm dễ kích ứng toner", "evidence_urls": ["https://www.threads.com/@a/post/1"]}],
                "affiliate_opportunities": [
                    {
                        "product": "Kem dưỡng phục hồi B5",
                        "purchase_intent": "cao",
                        "price": 249000,
                        "evidence_summary": "Nhiều bình luận hỏi xin link mua chính hãng",
                        "evidence_urls": ["https://www.threads.com/@b/post/2"],
                    }
                ],
                "content_suggestions": {
                    "text_posts": ["Checklist test kem B5 cho da nhạy cảm"],
                    "short_videos": ["3 dấu hiệu chọn nhầm toner khiến da kích ứng"],
                },
                "source_links": ["https://www.threads.com/@b/post/2"],
            },
        }
        res = self.store.ingest_raw_file(
            file_id="drv_rpt_1",
            file_name="muse_analysis_report.json",
            raw_bytes=json.dumps(muse_report_json).encode("utf-8"),
        )
        self.assertEqual(res["parsed"], 0)
        reports = self.store.list_reports()
        self.assertEqual(len(reports), 1)
        rep = reports[0]["report"]
        self.assertEqual(rep["source_type"], "muse_report_json")
        self.assertEqual(rep["affiliate_opportunities"][0]["product"], "Kem dưỡng phục hồi B5")
        self.assertEqual(rep["content_suggestions"]["short_videos"][0], "3 dấu hiệu chọn nhầm toner khiến da kích ứng")
        self.assertEqual(rep["data_quality"]["missing_sections"], [])

        # Report missing affiliate & content suggestions leaves them empty and marks missing_sections
        partial_rep = normalize_muse_report({"trending_highlights": ["Trend A"]}, file_name="partial.json")
        self.assertIn("affiliate_opportunities", partial_rep["data_quality"]["missing_sections"])
        self.assertIn("content_suggestions", partial_rep["data_quality"]["missing_sections"])

    def test_data_contract_v2_priority_upsert_and_non_crawl_isolation(self):
        """Data Contract V2: corrected_v2 updates in-place without inflating totals, original never overwrites corrected, nulls preserved, and non-crawl files isolated."""
        # 1. Non-crawl governance files MUST NOT become crawl items
        contract_doc = {
            "contract_version": "2.0",
            "schema_name": "muse_market_data_contract_v2",
            "platforms": ["threads", "tiktok", "shopee"],
        }
        strategy_doc = {
            "strategy_version": "2.0",
            "buckets": ["viral_content", "buyer_intent", "deal_affiliate"],
        }
        audit_doc = {
            "audit_id": "audit_e2e_quality_20261126",
            "run_id": "e2e-crawl-20261126-1559",
            "findings": ["Threads enrichment not attempted"],
        }
        for fid, fname, obj, expected_role in [
            ("f_contract", "data_contract_v2.json", contract_doc, "data_contract"),
            ("f_strategy", "crawl_strategy_v2.json", strategy_doc, "crawl_strategy"),
            ("f_audit", "audit_e2e_quality_20261126.json", audit_doc, "quality_audit"),
        ]:
            res = self.store.ingest_raw_file(
                file_id=fid,
                file_name=fname,
                raw_bytes=json.dumps(obj).encode("utf-8"),
            )
            self.assertEqual(res["file_role"], expected_role)
            self.assertEqual(res["inserted"], 0)

        self.assertEqual(self.store.get_stats()["total_items"], 0)
        self.assertEqual(len(self.store.list_reports()), 0)

        # 2. Original E2E files first (Threads, TikTok, Shopee)
        orig_threads = {
            "request_id": "crw_1795683337782_1fc1629a",
            "run_id": "e2e-crawl-20261126-1559",
            "platform": "threads",
            "status": "completed",
            "posts": [
                {
                    "post_id": "DgHdoHpmpJz",
                    "post_url": "https://www.threads.com/@thaomy_826/post/DgHdoHpmpJz",
                    "author_handle": "@thaomy_826",
                    "content_summary": "English summary should NOT beat accented Vietnamese summary when present.",
                    "content_summary_vi": "Review Black Friday Nguyễn Trãi có dấu",
                    "likes": 520,
                    "comment_count": 41,
                    "shares": None,
                    "views": None,
                    "content_group": "van_de_phan_nan",
                    "purchase_intent": "thap",
                }
            ],
        }
        orig_tiktok = {
            "run_id": "e2e-crawl-20261126-1559",
            "platform": "tiktok",
            "status": "partial",
            "posts": [
                {
                    "rank": 1,
                    "author_handle": "@th.official.channel",
                    "video_id": "7704957293997067538",
                    "views": 25100000,
                    "likes": None,
                    "content_summary": "Top 1 TikTok VN",
                },
                {
                    "rank": 4,
                    "author_handle": "@shopee_vn",
                    "video_id": None,
                    "views": None,
                    "likes": None,
                    "content_summary": "Shopee VN official channel",
                },
            ],
        }
        orig_shopee = {
            "run_id": "e2e-crawl-20261126-1559",
            "platform": "shopee",
            "status": "partial",
            "posts": [
                {
                    "product_name": "Kem duong da tay va mong Nuxe Reve de Miel 50ml",
                    "stock_signal": "CHI CON 3",
                    "price_vnd": None,
                    "product_url": None,
                    "evidence_url": "https://shopee.vn/flash_sale",
                    "content_summary": "Original ascii summary",
                }
            ],
        }

        self.store.ingest_raw_file("f_th_orig", "threads_crawl_e2e_20261126.json", json.dumps(orig_threads).encode("utf-8"))
        self.store.ingest_raw_file("f_tt_orig", "tiktok_crawl_e2e_20261126.json", json.dumps(orig_tiktok).encode("utf-8"))
        self.store.ingest_raw_file("f_sp_orig", "shopee_crawl_e2e_20261126.json", json.dumps(orig_shopee).encode("utf-8"))
        self.assertEqual(self.store.get_stats()["total_items"], 4)

        # 3. Corrected V2 files upgrade records IN-PLACE -> total_items stays 4!
        corr_threads = {
            **orig_threads,
            "engagement_completeness": {"likes_pct": 100, "comment_count_pct": 100, "shares_pct": 0},
            "posts": [
                {
                    **orig_threads["posts"][0],
                    "crawl_bucket": "viral_content",
                    "author_type": "nguoi_tieu_dung",
                    "content_summary_vi": "Review tiêu cực về Black Friday tại đường Nguyễn Trãi (Quận 1) đầy đủ dấu.",
                    "hook_analysis": {
                        "hook": "Đi review Black Friday để người theo dõi khỏi phải đi",
                        "insight": "Người mua mất niềm tin biển sale 99K",
                        "content_angle": "Kiểm chứng sale thật",
                    },
                    "data_quality": "usable",
                    "quality_issues": [],
                }
            ],
        }
        corr_tiktok = {
            **orig_tiktok,
            "posts": [
                {
                    "rank": 1,
                    "author_handle": "@th.official.channel",
                    "video_id": "7704957293997067538",
                    "video_url": "https://www.tiktok.com/@th.official.channel/video/7704957293997067538",
                    "views": 25100000,
                    "likes": None,
                    "content_summary_vi": "Video được xem nhiều nhất Việt Nam theo Endless (25,1 triệu lượt xem).",
                    "source_type": "third_party",
                    "url_status": "suy_ra_tu_video_id_va_handle_chua_kiem_chung",
                    "affiliate_relevance": "khong_ro",
                    "data_quality": "needs_verification",
                    "quality_issues": ["thieu_caption", "url_chua_kiem_chung"],
                },
                {
                    "rank": 4,
                    "author_handle": "@shopee_vn",
                    "video_id": None,
                    "video_url": None,
                    "views": None,
                    "likes": None,
                    "content_summary_vi": "Video từ tài khoản @shopee_vn — cần xác minh sản phẩm.",
                    "source_type": "third_party",
                    "url_status": "khong_xac_dinh",
                    "affiliate_relevance": "cao",
                    "data_quality": "needs_verification",
                    "quality_issues": ["thieu_video_id", "thieu_caption"],
                },
            ],
        }
        corr_shopee = {
            **orig_shopee,
            "posts": [
                {
                    "product_name": "Kem duong da tay va mong Nuxe Reve de Miel 50ml",
                    "stock_signal": "CHI CON 3",
                    "price_vnd": None,
                    "product_url": None,
                    "evidence_url": "https://shopee.vn/flash_sale",
                    "content_summary_vi": "Kem dưỡng da tay Nuxe trong Flash Sale đang diễn ra — thiếu giá & shop_id.",
                    "deal_status": "dang_dien_ra",
                    "frame_start_at": "2026-11-26T08:00:00Z",
                    "frame_end_at": "2026-11-26T10:00:00Z",
                    "price_status": "khong_doc_duoc",
                    "shop_id": None,
                    "item_id": None,
                    "sold_count": None,
                    "rating": None,
                    "data_quality": "needs_verification",
                    "affiliate_eligible": False,
                    "quality_issues": ["thieu_shop_id", "thieu_gia"],
                }
            ],
        }

        r_th = self.store.ingest_raw_file("f_th_corr", "threads_crawl_e2e_20261126_corrected_v2.json", json.dumps(corr_threads).encode("utf-8"))
        r_tt = self.store.ingest_raw_file("f_tt_corr", "tiktok_crawl_e2e_20261126_corrected_v2.json", json.dumps(corr_tiktok).encode("utf-8"))
        r_sp = self.store.ingest_raw_file("f_sp_corr", "shopee_crawl_e2e_20261126_corrected_v2.json", json.dumps(corr_shopee).encode("utf-8"))
        self.assertGreaterEqual(r_th["updated"], 1)
        self.assertGreaterEqual(r_tt["updated"], 1)
        self.assertGreaterEqual(r_sp["updated"], 1)
        self.assertEqual(self.store.get_stats()["total_items"], 4)

        # 4. Re-ingesting original files later MUST NOT downgrade corrected records!
        orig_th_again = {
            **orig_threads,
            "posts": [{**orig_threads["posts"][0], "content_summary": "Older English overwrite attempt", "content_summary_vi": None}],
        }
        r_downgrade = self.store.ingest_raw_file("f_th_orig_v2", "threads_crawl_e2e_20261126_rerun.json", json.dumps(orig_th_again).encode("utf-8"))
        self.assertEqual(r_downgrade["updated"], 0)
        self.assertEqual(self.store.get_stats()["total_items"], 4)

        # Verify accented Vietnamese summary, views preservation, null preservation, and needs_verification isolation
        th_items = self.store.query_items(platform="threads")["items"]
        self.assertTrue(th_items[0]["is_corrected"])
        self.assertIn("Nguyễn Trãi", th_items[0]["summary_vi"])
        self.assertIn("Nguyễn Trãi", th_items[0]["text_content"])
        self.assertIsNone(th_items[0]["title"])
        self.assertEqual(th_items[0]["extra_meta"]["crawl_bucket"], "viral_content")

        tt_items = self.store.query_items(platform="tiktok", sort_by="views")["items"]
        self.assertEqual(tt_items[0]["views_count"], 25100000)
        self.assertIsNone(tt_items[0]["likes_count"])  # NEVER fabricated as 0
        self.assertEqual(tt_items[0]["data_quality"], "needs_verification")
        self.assertEqual(self.store.query_items(platform="tiktok", affiliate_only=True)["total"], 0)

        sp_items = self.store.query_items(platform="shopee")["items"]
        self.assertEqual(sp_items[0]["extra_meta"]["deal_status"], "dang_dien_ra")
        self.assertEqual(sp_items[0]["extra_meta"]["price_status"], "khong_doc_duoc")
        self.assertIsNone(sp_items[0]["price"])  # NEVER fabricated as 0
        self.assertEqual(self.store.query_items(platform="shopee", affiliate_only=True)["total"], 0)

        # 5. Corrected Muse Report takes precedence & excludes needs_verification/platform tokens
        corr_report = {
            "report": "Bao cao phan tich E2E - BAN CORRECTED V2",
            "request_id": "crw_1795683337782_1fc1629a",
            "run_id": "e2e-crawl-20261126-1559",
            "corrects_report": "market_report_e2e_20261126.json",
            "technical_status": "completed",
            "data_quality_status": "dat mot phan",
            "totals": {"records": 20, "business_usable_records": 9, "needs_verification_records": 11},
            "platform_results": {
                "threads": {"technical_status": "completed", "data_quality_status": "dat mot phan", "records": 10, "business_usable_records": 9},
                "tiktok": {"technical_status": "partial", "data_quality_status": "khong dat", "records": 5, "business_usable_records": 0},
                "shopee": {"technical_status": "partial", "data_quality_status": "khong dat", "records": 5, "business_usable_records": 0},
            },
            "telegram_summary": "BÁO CÁO E2E — BẢN CORRECTED V2",
        }
        orig_report = {
            "report": "Old Original E2E Report",
            "run_id": "e2e-crawl-20261126-1559",
            "technical_status": "completed",
            "totals": {"records": 20, "business_usable_records": 20, "needs_verification_records": 0},
        }
        self.store.ingest_raw_file("f_rpt_orig", "market_report_e2e_20261126.json", json.dumps(orig_report).encode("utf-8"))
        self.store.ingest_raw_file("f_rpt_corr", "market_report_e2e_20261126_corrected_v2.json", json.dumps(corr_report).encode("utf-8"))

        reports = self.store.list_reports(limit=5)
        self.assertTrue(reports[0]["is_corrected"])
        ev = self.store.build_evidence_report(platform="all")
        self.assertEqual(ev["data_quality"]["data_quality_status"], "dat mot phan")
        self.assertEqual(ev["data_quality"]["usable_items"], 1)  # only threads item is usable in this test DB
        self.assertEqual(len(ev["affiliate_opportunities"]), 0)  # all unverified Shop/TikTok items excluded

    def test_newer_run_report_beats_older_corrected_report(self):
        """Regression (Issue #1): Older corrected report MUST NOT shadow a newer run's report; corrected wins only within the same run."""
        older_corrected = {
            "report": "Old Run 1 Report (Corrected)",
            "run_id": "run-20261126-old",
            "ended_at": "2026-11-26T09:00:00Z",
            "corrects_report": "market_report_run1.json",
            "technical_status": "completed",
            "data_quality_status": "dat",
            "telegram_summary": "OLD RUN 1",
        }
        newer_original = {
            "report": "New Run 2 Report (Original)",
            "run_id": "run-20261127-new",
            "ended_at": "2026-11-27T09:00:00Z",
            "technical_status": "completed",
            "data_quality_status": "dat mot phan",
            "telegram_summary": "NEW RUN 2",
        }
        # Ingest newer original FIRST, then older corrected SECOND
        self.store.ingest_raw_file(
            "f_new_orig",
            "market_report_run2_20261127.json",
            json.dumps(newer_original).encode("utf-8"),
            modified_time="2026-11-27T09:05:00Z",
        )
        self.store.ingest_raw_file(
            "f_old_corr",
            "market_report_run1_20261126_corrected_v2.json",
            json.dumps(older_corrected).encode("utf-8"),
            modified_time="2026-11-26T09:05:00Z",
        )

        reports = self.store.list_reports(limit=5)
        self.assertEqual(reports[0]["run_id"], "run-20261127-new")
        ev = self.store.build_evidence_report(platform="all")
        self.assertEqual(ev["latest_muse_report"]["run_id"], "run-20261127-new")

        # Within run-20261127-new, adding its own corrected version overrides run 2 original and stays #1
        newer_corrected = {
            **newer_original,
            "report": "New Run 2 Report (Corrected)",
            "corrects_report": "market_report_run2_20261127.json",
            "telegram_summary": "NEW RUN 2 CORRECTED",
        }
        self.store.ingest_raw_file(
            "f_new_corr",
            "market_report_run2_20261127_corrected_v2.json",
            json.dumps(newer_corrected).encode("utf-8"),
            modified_time="2026-11-27T09:10:00Z",
        )
        reports_after = self.store.list_reports(limit=5)
        self.assertEqual(reports_after[0]["run_id"], "run-20261127-new")
        self.assertTrue(reports_after[0]["is_corrected"])

    def test_threads_trend_v31_real_drive_fixtures_and_backward_compatibility(self):
        """V3.1: Real threads_trend_* + trend_report_* + data_contract_v3 coexist with V1/V2 history without overwriting or duplicating."""
        from market_intelligence.report_normalizer import normalize_muse_report

        # 1. Seed V1/V2 historical records first
        poc2_bytes = self._load_fixture_bytes("threads_crawl_poc_002.json")
        r_old = self.store.ingest_raw_file(
            "drv_old_poc2",
            "threads_crawl_poc_002.json",
            poc2_bytes,
            modified_time="2026-11-26T03:15:20Z",
        )
        hist_count = self.store.get_stats()["total_items"]
        self.assertEqual(hist_count, 10)
        self.assertEqual(r_old["inserted"], 10)

        # 2. V3.1 non-crawl governance / capability / discovery files MUST NOT insert items or create fake market reports
        contract_v3_bytes = self._load_fixture_bytes("data_contract_v3.json")
        discovery_doc = {"report": "Nghien cuu nguon", "sources": [{"rank": 1, "source": "threads-cli"}]}
        cap_test_doc = {"report": "Kiem chung threads-cli", "results": [{"command": "trends"}], "engagement_verification_map": {"likes": "Co"}}
        strategy_v3_doc = {"strategy": "Crawl V3", "version": "3.1", "trend_tiers": {"very_hot": "likes >= 1000"}}

        for fid, fname, raw_b, role in [
            ("f_c_v3", "data_contract_v3.json", contract_v3_bytes, "data_contract"),
            ("f_s_v3", "crawl_strategy_v3.json", json.dumps(strategy_v3_doc).encode("utf-8"), "crawl_strategy"),
            ("f_disc", "threads_discovery_sources_20261126.json", json.dumps(discovery_doc).encode("utf-8"), "research_doc"),
            ("f_cap", "threads_cli_capability_test_20261126.json", json.dumps(cap_test_doc).encode("utf-8"), "capability_test"),
        ]:
            n_res = self.store.ingest_raw_file(fid, fname, raw_b, modified_time="2026-11-26T15:51:31Z")
            self.assertEqual(n_res["file_role"], role)
            self.assertEqual(n_res["inserted"], 0)
        self.assertEqual(self.store.list_reports(limit=5), [])

        # 3. Ingest REAL threads_trend_20261126.json from Drive -> preserves 10 historical V1/V2 + adds 8 V3.1
        v3_trend_bytes = self._load_fixture_bytes("threads_trend_20261126.json")
        r_v3 = self.store.ingest_raw_file(
            "f_v3_trend",
            "threads_trend_20261126.json",
            v3_trend_bytes,
            modified_time="2026-11-26T15:51:33Z",
            folder_path="Threads",
        )
        self.assertEqual(r_v3["file_role"], "crawl_data")
        self.assertEqual(r_v3["parsed"], 8)
        self.assertEqual(r_v3["inserted"], 8)
        self.assertEqual(self.store.get_stats()["total_items"], hist_count + 8)

        # Duplicate ingest of the same V3.1 file inserts/updates ZERO new records
        r_v3_dup = self.store.ingest_raw_file(
            "f_v3_trend_dup",
            "threads_trend_20261126.json",
            v3_trend_bytes,
            modified_time="2026-11-26T15:51:33Z",
            folder_path="Threads",
        )
        self.assertEqual(r_v3_dup["inserted"], 0)
        self.assertEqual(r_v3_dup["updated"], 0)
        self.assertEqual(r_v3_dup["deduped"], 8)
        self.assertEqual(self.store.get_stats()["total_items"], hist_count + 8)

        # Verify distinct metrics: reposts_count & quotes_count preserved while shares/views stay None
        hoang_dung = self.store.query_items(platform="threads", trend_tier="very_hot", sort_by="engagement")["items"]
        self.assertEqual(len(hoang_dung), 4)
        self.assertEqual(hoang_dung[0]["post_id"], "DgM4DJRGhGL")
        self.assertEqual(hoang_dung[0]["likes_count"], 14906)
        self.assertEqual(hoang_dung[0]["reposts_count"], 1579)
        self.assertEqual(hoang_dung[0]["quotes_count"], 48)
        self.assertIsNone(hoang_dung[0]["shares_count"])  # Never conflated with reposts
        self.assertIsNone(hoang_dung[0]["views_count"])
        self.assertEqual(hoang_dung[0]["verification_status"], "cross_verified_web")
        self.assertEqual(hoang_dung[0]["trend_group"], "trend_toan_thi_truong")
        self.assertIn("velocity_note", hoang_dung[0]["extra_meta"]["viral_analysis"])

        # Add 1 hot + 1 unverified record in another test run to verify strict 5-tier separation & exclusion of unverified
        extra_v3 = {
            "run_id": "threads-trend-v3-extra",
            "run_status": "completed",
            "records": [
                {
                    "post_id": "HotPost1",
                    "post_url": "https://www.threads.com/@hot/post/HotPost1",
                    "author_handle": "@hot",
                    "content_summary_vi": "Bài đạt ngưỡng Hot (>= 300 likes).",
                    "likes": 450,
                    "comment_count": 30,
                    "reposts": 12,
                    "quotes": 2,
                    "shares": None,
                    "views": None,
                    "verification_status": "connector_verified",
                    "trend_tier": "hot",
                    "trend_group": "trend_affiliate",
                    "purchase_intent": "cao",
                    "data_quality": "usable",
                },
                {
                    "post_id": "UnverifPost1",
                    "post_url": "https://www.threads.com/@unv/post/UnverifPost1",
                    "author_handle": "@unv",
                    "content_summary_vi": "Bài chưa xác minh.",
                    "likes": 2500,
                    "verification_status": "unverified",
                    "trend_tier": "unverified",
                    "trend_group": "trend_affiliate",
                    "purchase_intent": "cao",
                    "data_quality": "needs_verification",
                },
            ],
        }
        self.store.ingest_raw_file("f_v3_extra", "threads_trend_20261127.json", json.dumps(extra_v3).encode("utf-8"))
        self.assertEqual(self.store.query_items(platform="threads", trend_tier="hot")["total"], 1)
        self.assertEqual(self.store.query_items(platform="threads", trend_tier="emerging")["total"], 2)
        self.assertEqual(self.store.query_items(platform="threads", trend_tier="watchlist")["total"], 2)
        self.assertEqual(self.store.query_items(platform="threads", trend_tier="unverified")["total"], 1)
        # Historical V1/V2 rows remain queryable under `legacy`
        self.assertEqual(self.store.query_items(platform="threads", trend_tier="legacy")["total"], 10)
        # Unverified post with likes=2500 MUST NEVER leak into very_hot or affiliate_only
        vh_ids = {x["post_id"] for x in self.store.query_items(platform="threads", trend_tier="very_hot")["items"]}
        self.assertNotIn("UnverifPost1", vh_ids)
        aff_ids = {x["post_id"] for x in self.store.query_items(platform="threads", affiliate_only=True, limit=100)["items"]}
        self.assertIn("HotPost1", aff_ids)
        self.assertNotIn("UnverifPost1", aff_ids)

        # 4. Ingest REAL trend_report_v3_20261126.json from Drive -> normalizes all 9 sections
        v3_rpt_bytes = self._load_fixture_bytes("trend_report_v3_20261126.json")
        r_rpt = self.store.ingest_raw_file(
            "f_v3_rpt",
            "trend_report_v3_20261126.json",
            v3_rpt_bytes,
            modified_time="2026-11-26T15:52:00Z",
            folder_path="Reports",
        )
        self.assertEqual(r_rpt["file_role"], "market_report")
        self.assertEqual(r_rpt["parsed"], 0)
        ev = self.store.build_evidence_report(platform="threads")
        self.assertEqual(ev["schema_version"], "v3.1")
        self.assertEqual(ev["run_status"], "completed")
        self.assertFalse(ev["is_source_error"])
        self.assertEqual(ev["screening"]["scanned_count"], 14)
        self.assertEqual(ev["screening"]["qualified_count"], 8)
        self.assertEqual(ev["tier_distribution"]["tiers"]["very_hot"], 4)
        self.assertEqual(len(ev["top_viral"]), 4)
        self.assertGreaterEqual(len(ev["affiliate_opportunities"]), 3)
        self.assertEqual(ev["affiliate_opportunities"][0]["priority"], "cao nhất")
        self.assertGreaterEqual(len(ev["priority_actions"]), 3)
        self.assertTrue(ev["telegram_summary"].startswith("TRẠNG THÁI: completed"))
        self.assertIn("Dr.Althea 345", ev["telegram_summary"])
        self.assertGreater(len(ev["trends_snapshot"]["topics"]), 10)

        # 5. Distinguish source_error vs no_qualifying_posts explicitly
        err_rep = normalize_muse_report(
            {"run_id": "r_err", "run_status": "source_error", "telegram_summary": "TRẠNG THÁI: source_error"},
            file_name="trend_report_err.json",
        )
        self.assertTrue(err_rep["is_source_error"])
        self.assertFalse(err_rep["is_empty_market_valid"])
        self.assertIn("Lỗi nguồn", err_rep["run_status_label_vi"])

        empty_ok_rep = normalize_muse_report(
            {"run_id": "r_empty", "run_status": "no_qualifying_posts", "sections": {"1_screening": {"scanned_count": 20, "qualified_count": 0}}},
            file_name="trend_report_empty.json",
        )
        self.assertFalse(empty_ok_rep["is_source_error"])
        self.assertTrue(empty_ok_rep["is_empty_market_valid"])
        self.assertIn("Không có bài đạt chuẩn", empty_ok_rep["run_status_label_vi"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
