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

    def test_crawl_time_ordering_ignores_newer_modified_time_and_keeps_statuses_distinct(self):
        """Regression (Issues #1, #7, #8): Older run with newer modifiedTime never beats newer crawl; Archive ignored; statuses remain distinct."""
        older_touched = {
            "report": "Old Run Retouched Later",
            "run_id": "run-old-20261125",
            "ended_at": "2026-11-25T10:00:00Z",
            "run_status": "completed",
            "technical_status": "completed",
            "data_quality_status": "dat",
            "telegram_summary": "OLD RUN TOUCHED LATER",
        }
        newer_crawl = {
            "report": "Newer Crawl V3.1 Report",
            "run_id": "run-new-20261126",
            "generated_at": "2026-11-26T22:52:00+07:00",
            "run_status": "completed",
            "telegram_summary": "NEWER CRAWL WINS",
        }
        newer_crawl_rev2 = {
            **newer_crawl,
            "report": "Newer Crawl V3.1 Report Rev 2",
            "corrects_report": "trend_report_new_20261126.json",
            "telegram_summary": "NEWER CRAWL REV 2",
        }
        # Newer crawl synced earlier, older run retouched days later with much newer modified_time
        self.store.ingest_raw_file(
            "f_new_1",
            "trend_report_new_20261126.json",
            json.dumps(newer_crawl).encode("utf-8"),
            modified_time="2026-11-26T16:00:00Z",
            folder_path="Reports",
        )
        self.store.ingest_raw_file(
            "f_old_touched",
            "market_report_old_20261125.json",
            json.dumps(older_touched).encode("utf-8"),
            modified_time="2026-11-29T23:59:59Z",
            folder_path="Reports",
        )

        reports = self.store.list_reports(limit=5)
        self.assertEqual(reports[0]["run_id"], "run-new-20261126")
        ev = self.store.build_evidence_report(platform="all")
        self.assertEqual(ev["latest_muse_report"]["run_id"], "run-new-20261126")
        # Issue #8: run_status is 'completed' while missing technical_status / data_quality_status stay None (not fabricated!)
        self.assertEqual(ev["run_status"], "completed")
        self.assertIsNone(ev["technical_status"])
        self.assertIsNone(ev["data_quality_status"])

        # Revision within same run picks newer modified_time/corrected
        self.store.ingest_raw_file(
            "f_new_2",
            "trend_report_new_20261126_corrected_v2.json",
            json.dumps(newer_crawl_rev2).encode("utf-8"),
            modified_time="2026-11-26T16:10:00Z",
            folder_path="Reports",
        )
        reports_rev = self.store.list_reports(limit=5)
        self.assertEqual(reports_rev[0]["run_id"], "run-new-20261126")
        self.assertTrue(reports_rev[0]["is_corrected"])

        # Issue #7: Direct ingest under Archive/ must not alter active counts or wipe historical rows
        before_total = self.store.get_stats()["total_items"]
        arch_res = self.store.ingest_raw_file(
            "f_direct_arch",
            "threads_trend_arch.json",
            json.dumps({"run_id": "arch", "records": [{"post_id": "ARCH1", "post_url": "https://www.threads.com/@a/post/ARCH1", "likes": 999}]}).encode("utf-8"),
            folder_path="Threads/Archive",
        )
        self.assertEqual(arch_res["file_role"], "archived")
        self.assertEqual(self.store.get_stats()["total_items"], before_total)

    def test_cross_run_dedup_three_runs_snapshots_and_activity_filters(self):
        """Cross-run dedup across 3 consecutive runs keeps 1 primary item, 3 snapshots, preserves old metrics, handles canonical_url fallback, concurrency, and filters."""
        # Run 1: Post A (with views/likes) + Post B (missing views)
        run1 = {
            "run_id": "run-cross-001",
            "platform": "threads",
            "crawled_at": "2026-11-27T08:00:00Z",
            "records": [
                {
                    "post_id": "CROSS_POST_A",
                    "post_url": "https://www.threads.com/@creator/post/CROSS_POST_A?xmt=111",
                    "author_handle": "@creator",
                    "content_summary_vi": "Chủ đề A lượt 1",
                    "views": 1000,
                    "likes": 50,
                    "comment_count": 5,
                    "trend_tier": "hot",
                    "data_quality": "usable",
                },
                {
                    "post_id": "CROSS_POST_B",
                    "post_url": "https://www.threads.com/@creator/post/CROSS_POST_B",
                    "author_handle": "@creator",
                    "content_summary_vi": "Bài thiếu views lượt 1",
                    "views": None,
                    "likes": 15,
                    "trend_tier": "watchlist",
                    "data_quality": "usable",
                },
            ],
        }
        r1 = self.store.ingest_raw_file("f_run_1", "threads_trend_run1.json", json.dumps(run1).encode("utf-8"))
        self.assertEqual(r1["new_count"], 2)
        self.assertEqual(r1["duplicate_count"], 0)
        self.assertEqual(r1["engagement_updated_count"], 0)
        self.assertEqual(r1["missing_views_count"], 1)
        self.assertEqual(self.store.get_stats()["total_items"], 2)

        # Concurrent duplicate sync of exact same file -> 0 extra snapshots!
        r1_dup_sync = self.store.ingest_raw_file("f_run_1", "threads_trend_run1.json", json.dumps(run1).encode("utf-8"))
        self.assertEqual(r1_dup_sync["new_count"], 0)
        post_a_item = [it for it in self.store.query_items(limit=20)["items"] if it["post_id"] == "CROSS_POST_A"][0]
        first_seen_run1 = post_a_item["first_seen_at"]
        self.assertEqual(len(self.store.query_item_snapshots(post_a_item["id"])), 1)

        # Run 2: Post A grew (views=3200, likes=180), Post B unchanged, Post C only has canonical URL without post_id
        run2 = {
            "run_id": "run-cross-002",
            "platform": "threads",
            "crawled_at": "2026-11-27T14:00:00Z",
            "records": [
                {
                    "post_id": "CROSS_POST_A",
                    "post_url": "https://www.threads.net/@creator/post/CROSS_POST_A",
                    "author_handle": "@creator",
                    "content_summary_vi": "Chủ đề A lượt 2",
                    "views": 3200,
                    "likes": 180,
                    "comment_count": 20,
                    "trend_tier": "very_hot",
                    "data_quality": "usable",
                },
                {
                    "post_id": "CROSS_POST_B",
                    "post_url": "https://www.threads.com/@creator/post/CROSS_POST_B",
                    "author_handle": "@creator",
                    "content_summary_vi": "Bài thiếu views lượt 2",
                    "views": None,
                    "likes": 15,
                    "trend_tier": "watchlist",
                    "data_quality": "usable",
                },
                {
                    "post_url": "https://www.threads.com/@canon/post/CANON_ONLY_1/?xmt=run2",
                    "author_handle": "@canon",
                    "content_summary_vi": "Bài C dùng canonical_url làm fallback",
                    "views": 500,
                    "likes": 25,
                    "trend_tier": "emerging",
                    "data_quality": "usable",
                },
            ],
        }
        r2 = self.store.ingest_raw_file("f_run_2", "threads_trend_run2.json", json.dumps(run2).encode("utf-8"))
        self.assertEqual(r2["new_count"], 1)  # Only Post C is new!
        self.assertEqual(r2["engagement_updated_count"], 1)  # Post A updated
        self.assertEqual(r2["duplicate_count"], 1)  # Post B unchanged
        self.assertEqual(r2["missing_views_count"], 1)  # Post B missing views
        self.assertEqual(self.store.get_stats()["total_items"], 3)

        # Run 3: Post A appears again with identical metrics (duplicate, NOT new, NOT lost old snapshot); Post C appears on threads.net with updated views
        run3 = {
            "run_id": "run-cross-003",
            "platform": "threads",
            "crawled_at": "2026-11-27T20:00:00Z",
            "records": [
                {
                    "post_id": "CROSS_POST_A",
                    "post_url": "https://www.threads.com/@creator/post/CROSS_POST_A?igshid=99",
                    "author_handle": "@creator",
                    "content_summary_vi": "Chủ đề A lượt 3",
                    "views": 3200,
                    "likes": 180,
                    "comment_count": 20,
                    "trend_tier": "very_hot",
                    "data_quality": "usable",
                },
                {
                    "post_url": "https://threads.net/@canon/post/CANON_ONLY_1?ref=web",
                    "author_handle": "@canon",
                    "content_summary_vi": "Bài C tăng trưởng qua threads.net",
                    "views": 950,
                    "likes": 60,
                    "trend_tier": "hot",
                    "data_quality": "usable",
                },
            ],
        }
        r3 = self.store.ingest_raw_file("f_run_3", "threads_trend_run3.json", json.dumps(run3).encode("utf-8"))
        self.assertEqual(r3["new_count"], 0)
        self.assertEqual(r3["duplicate_count"], 1)  # Post A unchanged in Run 3
        self.assertEqual(r3["engagement_updated_count"], 1)  # Post C matched by canonical_url & updated

        # Verify: only 1 primary record per post (total 3), and Post A has exactly 3 chronological snapshots preserving Run 1's 1000/50
        stats = self.store.get_stats()
        self.assertEqual(stats["total_items"], 3)
        detail_a = self.store.get_item_detail(post_a_item["id"])
        self.assertEqual(detail_a["first_seen_at"], first_seen_run1)
        self.assertEqual(detail_a["snapshot_count"], 3)
        snaps_a = detail_a["snapshots"]
        self.assertEqual(len(snaps_a), 3)
        self.assertEqual([s["change_type"] for s in snaps_a], ["new", "updated", "duplicate"])
        self.assertEqual(snaps_a[0]["views_count"], 1000)
        self.assertEqual(snaps_a[1]["views_count"], 3200)
        self.assertEqual(snaps_a[1]["views_delta"], 2200)
        self.assertEqual(snaps_a[1]["likes_delta"], 130)
        self.assertEqual(snaps_a[2]["views_count"], 3200)
        self.assertEqual(snaps_a[2]["views_delta"], 0)

        # Verify activity filters: 'new' has 0 items now (all seen >=2 runs!), 'growth' has A & C (2 items), 'all' has 3 items
        self.assertEqual(self.store.query_items(activity_filter="new")["total"], 0)
        growth_res = self.store.query_items(activity_filter="growth")
        self.assertEqual(growth_res["total"], 2)
        growth_pids = {it["post_id"] for it in growth_res["items"]}
        self.assertIn("CROSS_POST_A", growth_pids)
        self.assertEqual(self.store.query_items(activity_filter="all")["total"], 3)

        # Ingest a brand new Post D in Run 4 -> activity_filter='new' shows only Post D
        run4 = {
            "run_id": "run-cross-004",
            "platform": "threads",
            "crawled_at": "2026-11-27T21:00:00Z",
            "records": [{"post_id": "CROSS_POST_D", "post_url": "https://threads.net/@new/post/CROSS_POST_D", "views": 400, "likes": 12}],
        }
        r4 = self.store.ingest_raw_file("f_run_4", "threads_trend_run4.json", json.dumps(run4).encode("utf-8"))
        self.assertEqual(r4["new_count"], 1)
        new_only = self.store.query_items(activity_filter="new")
        self.assertEqual(new_only["total"], 1)
        self.assertEqual(new_only["items"][0]["post_id"], "CROSS_POST_D")

    def test_v32_views_display_shares_reposts_reconciliation_idempotency_and_seed171(self):
        """V3.2: 513K views_display (no 513 truncation), separate shares/reposts, new vs new_qualified (35/50), 2-laptop snapshot idempotency, URL->post_id merge, 0004 seed 171, and report reconciliation."""
        import sqlite3
        from market_intelligence.normalizer import extract_v32_counters
        from market_intelligence.report_normalizer import normalize_muse_report

        # 1) Views 513K + display_approx + separate shares/reposts + absent views honesty
        v32_posts = {
            "run_id": "run-v32-views-01",
            "platform": "threads",
            "crawled_at": "2026-11-27T09:00:00Z",
            "records": [
                {
                    "post_id": "V32_513K",
                    "post_url": "https://www.threads.com/@myqnam/post/V32_513K?ref=1",
                    "author_handle": "@myqnam",
                    "summary_vi": "Bài viral 513K views",
                    "views": 513000,
                    "views_display": "513K",
                    "views_precision": "display_approx",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-27T09:00:00Z",
                    "views_evidence_url": "https://threads.net/@myqnam/post/V32_513K",
                    "likes": 1200,
                    "comments": 95,
                    "shares": 88,
                    "reposts": 41,
                    "data_quality": "usable",
                },
                {
                    "post_id": "V32_DISPLAY_ONLY",
                    "post_url": "https://threads.net/@nom/post/V32_DISPLAY_ONLY",
                    "author_handle": "@nom",
                    "summary_vi": "Chỉ có views_display không có integer",
                    "views": "513K",
                    "views_display": "513K",
                    "views_precision": "display_approx",
                    "likes": 45,
                    "shares": 7,
                    "reposts": 2,
                    "verification_status": "unverified",
                },
                {
                    "post_url": "https://www.threads.net/@canonmerge/post/MERGE77?x=1",
                    "author_handle": "@canonmerge",
                    "summary_vi": "URL xuất hiện trước khi biết post_id",
                    "views": 3600,
                    "views_display": "3.6K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-27T09:00:00Z",
                    "views_evidence_url": "https://threads.net/@canonmerge/post/MERGE77",
                    "likes": 40,
                    "shares": 3,
                    "reposts": 9,
                    "data_quality": "usable",
                },
            ],
        }
        res_1 = self.store.ingest_raw_file("f_v32_1", "threads_trend_v32_1.json", json.dumps(v32_posts).encode("utf-8"))
        self.assertEqual(res_1["new_count"], 3)
        self.assertEqual(res_1["new_qualified_count"], 2)  # V32_DISPLAY_ONLY lacks integer views & is unverified!
        self.assertEqual(res_1["missing_views_count"], 1)

        items_map = {it["post_id"] or it["canonical_url"]: it for it in self.store.query_items(limit=20)["items"]}
        it_513 = items_map["V32_513K"]
        self.assertEqual(it_513["views_count"], 513000)
        self.assertEqual(it_513["views_display"], "513K")
        self.assertEqual(it_513["views_precision"], "display_approx")
        self.assertEqual(it_513["views_source"], "threads_web_browser")
        self.assertEqual(it_513["views_measured_at"], "2026-11-27T09:00:00Z")
        self.assertEqual(it_513["views_evidence_url"], "https://threads.net/@myqnam/post/V32_513K")
        self.assertEqual(it_513["shares_count"], 88)
        self.assertEqual(it_513["reposts_count"], 41)

        it_disp = items_map["V32_DISPLAY_ONLY"]
        self.assertIsNone(it_disp["views_count"])  # "513K" MUST NOT be truncated to 513
        self.assertEqual(it_disp["views_display"], "513K")
        self.assertEqual(it_disp["shares_count"], 7)
        self.assertEqual(it_disp["reposts_count"], 2)

        # 2) Two laptops syncing the EXACT SAME run_id & measurement timestamp MUST keep only 1 snapshot
        self.store.ingest_raw_file("f_v32_laptop2", "threads_trend_v32_1_copy.json", json.dumps(v32_posts).encode("utf-8"), md5_checksum="md5_laptop2")
        detail_513 = self.store.get_item_detail(it_513["id"])
        self.assertEqual(len(detail_513["snapshots"]), 1)
        self.assertEqual(detail_513["snapshots"][0]["measured_at"], "2026-11-27T09:00:00Z")

        # 3) Later run discovers post_id for URL-first post MERGE77 -> merges into single item with 2 snapshots
        later_id_payload = {
            "run_id": "run-v32-views-02",
            "platform": "threads",
            "crawled_at": "2026-11-27T11:30:00Z",
            "records": [
                {
                    "post_id": "MERGE77",
                    "post_url": "https://threads.net/@canonmerge/post/MERGE77",
                    "views": 6400,
                    "likes": 92,
                    "shares": 5,
                    "reposts": 12,
                    "data_quality": "usable",
                }
            ],
        }
        res_2 = self.store.ingest_raw_file("f_v32_2", "threads_trend_v32_2.json", json.dumps(later_id_payload).encode("utf-8"))
        self.assertEqual(res_2["new_count"], 0)
        self.assertEqual(res_2["engagement_updated_count"], 1)
        self.assertEqual(self.store.get_stats()["total_items"], 3)
        merged_item = next(x for x in self.store.query_items(limit=20)["items"] if "MERGE77" in (x.get("canonical_url") or ""))
        self.assertEqual(merged_item["post_id"], "MERGE77")
        self.assertEqual(merged_item["snapshot_count"], 2)

        # 4) post_identity_index.json is classified as 'identity_index' and never leaks into items or reports
        raw_dir = os.path.join(ROOT_DIR, "data", "market_intelligence", "raw")
        idx_candidates = [f for f in os.listdir(raw_dir) if "post_identity_index" in f] if os.path.isdir(raw_dir) else []
        if idx_candidates:
            with open(os.path.join(raw_dir, idx_candidates[0]), "rb") as f:
                idx_res = self.store.ingest_raw_file("f_identity_idx", "post_identity_index.json", f.read())
        else:
            idx_mock = {"contract": "3.2", "primary_key": "post_id", "entries": [{"platform": "threads", "post_id": "IDX_1", "canonical_url": "https://threads.net/@x/post/IDX_1"}]}
            idx_res = self.store.ingest_raw_file("f_identity_idx", "post_identity_index.json", json.dumps(idx_mock).encode("utf-8"))
        self.assertEqual(idx_res["file_role"], "identity_index")
        self.assertEqual(self.store.get_stats()["total_items"], 3)

        # 5) V3.2 D1 migrations 0003 + 0004 (171 posts) execute cleanly & idempotently in SQLite
        m0003_path = os.path.join(ROOT_DIR, "cloudflare-worker", "migrations", "0003_cross_run_dedup_registry.sql")
        m0004_path = os.path.join(ROOT_DIR, "cloudflare-worker", "migrations", "0004_seed_post_identity_index_171.sql")
        conn = sqlite3.connect(":memory:")
        try:
            with open(m0003_path, "r", encoding="utf-8") as f3:
                conn.executescript(f3.read())
            with open(m0004_path, "r", encoding="utf-8") as f4:
                sql4 = f4.read()
            conn.executescript(sql4)
            conn.executescript(sql4)  # Re-apply must stay 171/171 idempotent
            reg_cnt = conn.execute("SELECT COUNT(*) FROM market_post_registry").fetchone()[0]
            snap_cnt = conn.execute("SELECT COUNT(*) FROM market_post_snapshots").fetchone()[0]
            self.assertEqual(reg_cnt, 171)
            self.assertEqual(snap_cnt, 171)
        finally:
            conn.close()

        # 6) V3.2 report counters reconciliation & sections mapping
        v32_report = {
            "report": "Báo cáo V3.2 Test",
            "run_id": "run-v32-rpt-35of50",
            "run_status": "partial",
            "generated_at": "2026-11-27T12:00:00Z",
            "counters": {
                "requested_count": 50,
                "scanned_count": 150,
                "unique_candidate_count": 120,
                "new_count": 40,
                "new_qualified_count": 35,
                "unverified_count": 15,
                "missing_views_count": 8,
                "duplicate_history_count": 50,
                "updated_metrics_count": 10,
                "rejected_count": 10,
                "similar_content_count": 6,
                "target_achieved": False,
                "stop_reason": "sources_exhausted",
            },
            "sections": {
                "3_trends": [{"theme": "Son dưỡng", "lifecycle_state": "growing", "why_hot": "Cao điểm mùa lạnh"}],
                "4_top_viral": [{"post": "@myqnam", "views": 513000, "views_display": "513K", "views_precision": "display_approx"}],
                "9_growth_tracking": [{"post": "@sandeal.shopee", "change_summary": "5K → 18K views"}],
                "10_priority_actions": [{"action": "Test son dưỡng 8h"}],
            },
        }
        extracted = extract_v32_counters(v32_report)
        self.assertEqual(extracted["new_qualified_count"], 35)
        self.assertEqual(extracted["new_count"], 40)
        self.assertTrue(extracted["counters_reconciled"])
        self.assertEqual(extracted["reconciliation_diff"], 0)
        self.assertFalse(extracted["target_achieved"])
        self.assertEqual(extracted["stop_reason"], "sources_exhausted")

        norm_rpt = normalize_muse_report(v32_report, file_name="trend_report_20261127_1200.json")
        self.assertEqual(norm_rpt["schema_version"], "v3.2")
        self.assertEqual(norm_rpt["counters"]["new_qualified_count"], 35)
        self.assertTrue(norm_rpt["counters"]["counters_reconciled"])
        self.assertEqual(norm_rpt["growth_tracking"][0]["post"], "@sandeal.shopee")
        self.assertEqual(norm_rpt["priority_actions"][0], "Test son dưỡng 8h")

        # 7) Point 2: Numeric views alone WITHOUT valid 5-field V3.2 provenance MUST NOT count in new_qualified_count
        incomplete_views_run = {
            "run_id": "run-v32-strict-qual",
            "platform": "threads",
            "crawled_at": "2026-11-27T12:15:00Z",
            "records": [
                {
                    "post_id": "STRICT_NUMERIC_ONLY",
                    "post_url": "https://threads.net/@s/post/STRICT_NUMERIC_ONLY",
                    "views": 45000,
                    "likes": 300,
                },
                {
                    "post_id": "STRICT_BAD_SRC",
                    "post_url": "https://threads.net/@s/post/STRICT_BAD_SRC",
                    "views": 45000,
                    "views_display": "45K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_cli_search",
                    "views_measured_at": "2026-11-27T12:15:00Z",
                    "views_evidence_url": "https://threads.net/@s/post/STRICT_BAD_SRC",
                },
                {
                    "post_id": "STRICT_FULL_PASS",
                    "post_url": "https://threads.net/@s/post/STRICT_FULL_PASS",
                    "views": 45000,
                    "views_display": "45K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-27T12:15:00Z",
                    "views_evidence_url": "https://threads.net/@s/post/STRICT_FULL_PASS",
                },
            ],
        }
        r_strict = self.store.ingest_raw_file("f_strict", "threads_trend_strict.json", json.dumps(incomplete_views_run).encode("utf-8"))
        self.assertEqual(r_strict["new_count"], 3)
        self.assertEqual(r_strict["new_qualified_count"], 1)  # Only STRICT_FULL_PASS satisfies all 5 views fields

        # 8) Point 3: Late-arriving older snapshot preserves chronological history without overwriting newer metrics/last_seen_at
        later_first = {
            "run_id": "run-t2-latest",
            "platform": "threads",
            "crawled_at": "2026-11-27T15:00:00Z",
            "records": [
                {
                    "post_id": "SQLITE_LATE_GUARD",
                    "post_url": "https://threads.net/@lg/post/SQLITE_LATE_GUARD",
                    "views": 99000,
                    "views_display": "99K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-27T15:00:00Z",
                    "views_evidence_url": "https://threads.net/@lg/post/SQLITE_LATE_GUARD",
                    "likes": 2500,
                }
            ],
        }
        older_late = {
            "run_id": "run-t1-backfill",
            "platform": "threads",
            "crawled_at": "2026-11-25T07:00:00Z",
            "records": [
                {
                    "post_id": "SQLITE_LATE_GUARD",
                    "post_url": "https://threads.net/@lg/post/SQLITE_LATE_GUARD",
                    "views": 1100,
                    "views_display": "1.1K",
                    "views_precision": "rounded_display",
                    "views_source": "threads_web_browser",
                    "views_measured_at": "2026-11-25T07:00:00Z",
                    "views_evidence_url": "https://threads.net/@lg/post/SQLITE_LATE_GUARD",
                    "likes": 20,
                }
            ],
        }
        self.store.ingest_raw_file("f_lg_t2", "threads_trend_lgt2.json", json.dumps(later_first).encode("utf-8"))
        self.store.ingest_raw_file("f_lg_t1", "threads_trend_lgt1.json", json.dumps(older_late).encode("utf-8"))
        lg_item = next(x for x in self.store.query_items(limit=50)["items"] if x.get("post_id") == "SQLITE_LATE_GUARD")
        lg_detail = self.store.get_item_detail(lg_item["id"])
        self.assertEqual(lg_detail["views_count"], 99000)
        self.assertEqual(lg_detail["likes_count"], 2500)
        self.assertEqual(lg_detail["last_seen_at"], "2026-11-27T15:00:00Z")
        self.assertEqual(lg_detail["first_seen_at"], "2026-11-25T07:00:00Z")
        self.assertEqual(lg_detail["snapshot_count"], 2)
        self.assertEqual(len(lg_detail["snapshots"]), 2)

        # 9) Point 3: URL->post_id re-sync of old run does NOT duplicate snapshot in SQLite
        resync_u1 = {
            "run_id": "run-v32-views-01",
            "platform": "threads",
            "crawled_at": "2026-11-27T09:00:00Z",
            "records": [{"post_id": "MERGE77", "post_url": "https://www.threads.net/@canonmerge/post/MERGE77?x=1", "views": 3600, "likes": 40}],
        }
        self.store.ingest_raw_file("f_resync_u1", "threads_trend_resync_u1.json", json.dumps(resync_u1).encode("utf-8"))
        merged_after = next(x for x in self.store.query_items(limit=50)["items"] if x.get("post_id") == "MERGE77")
        self.assertEqual(merged_after["snapshot_count"], 2)
        self.assertEqual(len(self.store.get_item_detail(merged_after["id"])["snapshots"]), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
