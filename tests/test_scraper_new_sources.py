"""The collectors added with the source catalogue: ASX announcements, World Bank,
EU tenders (TED), Mining People International, and the Apify connector.

Each is tested through its pure parser, against the shape the real service
returned when it was sampled (5 Oct 2026). Nothing here touches the network: a
test that depends on a live site is a test of the site.
"""
from __future__ import annotations

import asyncio

from scraper import apify, asx, catalog, miningpeople, newsfeed, ted, worldbank

REQUIRED = {"source_url", "raw_content", "captured_at", "source_name", "source_type", "geography"}


def _shape_ok(records):
    assert records, "expected at least one record"
    for r in records:
        assert REQUIRED <= set(r), r
        assert r["source_url"].startswith("http")
        assert r["raw_content"].strip()


# ---------- ASX announcements ----------

ASX_PAYLOAD = {"data": {"displayName": "MINERAL RESOURCES LIMITED", "symbol": "MIN", "items": [
    {"announcementType": "SECURITY HOLDER DETAILS", "date": "2026-09-30T08:26:25.000Z",
     "documentKey": "2924-1", "headline": "Appendix 3Y Notification", "isPriceSensitive": False},
    {"announcementType": "PROGRESS REPORT", "date": "2026-09-29T22:00:00.000Z",
     "documentKey": "2924-2", "headline": "Onslow Iron haul road contract awarded",
     "isPriceSensitive": True},
    {"announcementType": "PERIODIC REPORTS", "date": "2026-09-28T22:00:00.000Z",
     "documentKey": "2924-3", "headline": "September 2026 Quarterly Results Webcast Details",
     "isPriceSensitive": False},
    {"announcementType": "COMPANY ADMINISTRATION", "date": "2026-09-27T22:00:00.000Z",
     "documentKey": "2924-4", "headline": "Appointment of Chief Operating Officer",
     "isPriceSensitive": False},
    {"announcementType": "OTHER", "date": "2026-09-26T22:00:00.000Z",
     "documentKey": "", "headline": "No key, so no address to dedupe on", "isPriceSensitive": True},
]}}


def test_asx_keeps_what_could_move_hiring_and_drops_housekeeping():
    records = asx.parse_announcements(ASX_PAYLOAD, "MIN")
    _shape_ok(records)
    assert [r["title"] for r in records] == [
        "Onslow Iron haul road contract awarded",
        "Appointment of Chief Operating Officer",
    ]


def test_asx_records_name_the_company_and_the_exchange():
    rec = asx.parse_announcements(ASX_PAYLOAD, "MIN")[0]
    assert "Mineral Resources Limited (ASX: MIN)" in rec["raw_content"]
    assert "price sensitive" in rec["raw_content"]
    assert rec["source_name"] == "asx" and rec["source_type"] == "news"
    assert rec["geography"] == "AU"


def test_asx_addresses_are_unique_per_announcement():
    """Ingest dedupes on the address, and every announcement for a company
    would otherwise share the company's page."""
    urls = [r["source_url"] for r in asx.parse_announcements(ASX_PAYLOAD, "MIN")]
    assert len(set(urls)) == len(urls)
    assert all("/MIN#announcement-" in u for u in urls)


def test_asx_housekeeping_is_dropped_even_when_price_sensitive():
    assert asx.is_signal("Appendix 3Y Notification", True) is False
    assert asx.is_signal("Update - Notification of buy-back", True) is False


def test_asx_a_quiet_headline_survives_when_the_asx_flags_it():
    assert asx.is_signal("Market update", True) is True
    assert asx.is_signal("Market update", False) is False


def test_asx_empty_or_malformed_payloads_yield_nothing():
    assert asx.parse_announcements({}, "BHP") == []
    assert asx.parse_announcements({"data": None}, "BHP") == []


def test_asx_follows_the_built_in_list_until_one_is_set(panel):
    assert asx.tickers() == asx.DEFAULT_TICKERS
    panel.set_asx_tickers("BHP, RIO", changed_by="admin@example.com")
    assert asx.tickers() == ("BHP", "RIO")
    panel.set_asx_tickers("", changed_by="admin@example.com")
    assert asx.tickers() == asx.DEFAULT_TICKERS


# ---------- World Bank ----------

WB_NOTICES = {"procnotices": [
    {"id": "OP1", "notice_type": "Contract Award", "noticedate": "28-Sep-2026",
     "project_name": "Child Nutrition and Social Protection Project",
     "bid_description": "Supply and Delivery of Tablets for Household Surveys",
     "procurement_group": "GO", "procurement_method_name": "Direct Selection",
     "notice_text": "<div><h4>Contract Award</h4><p><b>Project:</b> P174637</p></div>"},
    {"id": "OP2", "notice_type": "Request for Expression of Interest", "noticedate": "20-Sep-2026",
     "project_name": "Resilient Transport Project",
     "bid_description": "Detailed assessment and design of bridges on Ramu Highway",
     "procurement_group": "CS", "procurement_method_name": "Quality And Cost-Based Selection",
     "notice_text": "<p>Consultant firm required.</p>"},
    {"id": "OP3", "notice_type": "Invitation for Bids", "noticedate": "12-Sep-2026",
     "project_name": "Urban Youth Employment Project",
     "bid_description": "Recreational park development - Phase 2",
     "procurement_group": "CW", "procurement_method_name": "Request for Bids"},
    {"id": "OP4", "notice_type": "Contract Award", "noticedate": "10-Sep-2026",
     "project_name": "Education Support Program",
     "bid_description": "Capacity building support for women's empowerment",
     "procurement_group": "CS"},
]}

WB_PROJECTS = {"projects": {
    "P173194": {"id": "P173194", "project_name": "National Energy Access Transformation Project",
                "totalamt": "200,000,000", "impagency": "PNG Power Limited", "status": "Active",
                "sector1": {"Name": "", "Percent": 0}, "boardapprovaldate": "2024-09-24T00:00:00Z",
                "url": "https://projects.worldbank.org/en/projects-operations/project-detail/P173194"},
    "P174594": {"id": "P174594", "project_name": "Enhancing Labor Mobility from Papua New Guinea",
                "totalamt": "32,000,000", "impagency": "Department of Treasury", "status": "Active",
                "sector1": {"Name": "Social Protection", "Percent": 83},
                "url": "https://projects.worldbank.org/en/projects-operations/project-detail/P174594"},
}}


def test_worldbank_keeps_works_and_infrastructure_and_drops_the_rest():
    records = worldbank.parse_notices(WB_NOTICES)
    _shape_ok(records)
    assert [r["source_url"].rsplit("/", 1)[1] for r in records] == ["OP2", "OP3"]


def test_worldbank_common_words_do_not_count_as_sectors():
    """"support", "empowerment" and "capacity building" contain "port", "power"
    and "building". Matching on fragments let through every health and
    education consultancy this filter exists to drop."""
    assert worldbank.is_relevant("Capacity building support for women's empowerment") is False
    assert worldbank.is_relevant("Upgrade of the port and power station") is True


def test_worldbank_civil_works_are_relevant_whatever_the_wording():
    assert worldbank.is_relevant("Recreational park development", group="CW") is True


def test_worldbank_notices_are_tenders_in_png():
    rec = worldbank.parse_notices(WB_NOTICES)[0]
    assert rec["source_type"] == "tender" and rec["geography"] == "PNG"
    assert "World Bank request for expression of interest" in rec["raw_content"]
    assert "<" not in rec["raw_content"], "notice HTML is stripped"


def test_worldbank_projects_are_filtered_the_same_way():
    records = worldbank.parse_projects(WB_PROJECTS)
    _shape_ok(records)
    assert [r["title"] for r in records] == ["National Energy Access Transformation Project"]
    assert "US$200,000,000 committed" in records[0]["raw_content"]


def test_worldbank_malformed_payloads_yield_nothing():
    assert worldbank.parse_notices({}) == []
    assert worldbank.parse_projects({"projects": {"P1": "not a dict"}}) == []


# ---------- EU tenders (TED) ----------

TED_PAYLOAD = {"notices": [
    {"publication-number": "501152-2026", "publication-date": "2026-07-20+02:00",
     "notice-type": "cn-standard",
     "notice-title": {"fra": "Services climatologiques", "eng": "Climatology services for PNG"},
     "buyer-name": {"fra": ["EXPERTISE FRANCE"]},
     "deadline-receipt-tender-date-lot": ["2026-08-17+02:00"]},
    {"publication-number": "116894-2021", "publication-date": "2021-03-09+01:00",
     "notice-type": "can-standard", "buyer-name": {"eng": ["Government of Papua New Guinea"]}},
    {"publication-date": "2026-01-01+01:00", "notice-title": {"eng": "No number, so no address"}},
]}


def test_ted_reads_titles_in_english_where_there_is_one():
    records = ted.parse_notices(TED_PAYLOAD)
    _shape_ok(records)
    assert records[0]["title"] == "Climatology services for PNG"
    assert records[0]["agency"] == "EXPERTISE FRANCE", "another language is still a name"


def test_ted_records_are_png_tenders_with_their_deadline():
    rec = ted.parse_notices(TED_PAYLOAD)[0]
    assert rec["source_type"] == "tender" and rec["geography"] == "PNG"
    assert rec["closes"] == "2026-08-17"
    assert rec["source_url"] == "https://ted.europa.eu/en/notice/-/detail/501152-2026"


def test_ted_a_notice_without_a_title_is_named_by_its_kind():
    rec = ted.parse_notices(TED_PAYLOAD)[1]
    assert "contract award" in rec["title"].lower()


def test_ted_a_notice_without_a_number_is_skipped():
    assert len(ted.parse_notices(TED_PAYLOAD)) == 2


# ---------- Mining People International ----------

MPI_HTML = """
<div class="card job-card mb-6"><div class="card-body p-6">
  <small class="text-danger text-uppercase"> Operators </small>
  <h3 class="h4"><a class="stretched-link" href="/job/details/40848/Blast-Hole-Driller-/40848-BH"> Blast Hole Driller </a></h3>
  <p class="text-muted"> Sandvik DPI or Leopard driller? Your next Goldfields job is waiting. </p>
</div></div>
<div class="card job-card mb-6"><div class="card-body p-6">
  <small class="text-danger text-uppercase"> Engineering </small>
  <h3 class="h4"><a class="stretched-link" href="/job/details/40907/Engineering-Manager/40907-TT">Engineering Manager</a></h3>
</div></div>
<div class="card job-card mb-6"><div class="card-body p-6"><h3><a href="/about-us">Not a job</a></h3></div></div>
"""


def test_miningpeople_reads_one_vacancy_per_card():
    records = miningpeople.parse_listing(MPI_HTML)
    _shape_ok(records)
    assert [r["title"] for r in records] == ["Blast Hole Driller", "Engineering Manager"]
    assert records[0]["source_url"] == \
        "https://www.mpirecruitment.au/job/details/40848/Blast-Hole-Driller-/40848-BH"


def test_miningpeople_records_are_au_job_ads_naming_the_board():
    rec = miningpeople.parse_listing(MPI_HTML)[0]
    assert rec["source_type"] == "job_board" and rec["geography"] == "AU"
    assert "Mining People International" in rec["raw_content"]
    assert "Operators" in rec["raw_content"] and "Goldfields" in rec["raw_content"]


def test_miningpeople_a_page_without_cards_yields_nothing():
    assert miningpeople.parse_listing("<html><body>No vacancies</body></html>") == []


# ---------- sponsored articles in a feed ----------

SPONSORED_FEED = """<rss><channel>
<item><title>Minimising risk: minimising tailings</title>
  <link>https://www.miningmonthly.com/partners/partner-content/4537673/minimising-risk</link></item>
<item><title>Gold project approved in Kalgoorlie</title>
  <link>https://www.miningmonthly.com/gold/news/4537700/gold-project-approved</link></item>
</channel></rss>"""


def test_sponsored_articles_never_reach_the_records():
    feed = newsfeed.Feed("Mining Monthly", "https://www.miningmonthly.com/feeds/rss", "AU",
                         "miningmonthly")
    records = newsfeed.parse_feed(SPONSORED_FEED, feed)
    assert [r["title"] for r in records] == ["Gold project approved in Kalgoorlie"]


def test_a_catalogued_feed_reports_under_its_own_name():
    feed = next(f for f in newsfeed.FEEDS if f.source == "miningmonthly")
    records = newsfeed.parse_feed(SPONSORED_FEED, feed)
    assert records[0]["source_name"] == "miningmonthly"
    assert records[0]["publication"] == "Mining Monthly"


# ---------- Apify ----------

INDEED = catalog.get("indeed")
TOKEN = "apify_api_test_only_0123456789abcdef"


def test_apify_reads_the_field_names_actors_commonly_use():
    items = [
        {"positionName": "Maintenance Planner", "company": "BHP", "location": "Newman WA",
         "url": "https://au.indeed.com/viewjob?jk=1", "description": "<p>FIFO 8/6 roster</p>",
         "postedAt": "2 days ago"},
        {"title": "Diesel Fitter", "companyName": "Downer", "jobLocation": {"city": "Perth"},
         "jobUrl": "https://au.indeed.com/viewjob?jk=2"},
    ]
    records = apify.parse_items(items, source_id="indeed", label="Indeed Australia", geography="AU")
    _shape_ok(records)
    assert [r["title"] for r in records] == ["Maintenance Planner", "Diesel Fitter"]
    assert records[0]["raw_content"] == "Maintenance Planner | BHP | Newman WA | FIFO 8/6 roster"
    assert records[1]["company"] == "Downer" and records[1]["location"] == "Perth"
    assert all(r["source_name"] == "indeed" and r["source_type"] == "job_board" for r in records)


def test_seek_is_read_through_an_actor_under_the_name_it_always_had():
    seek = catalog.get("seek")
    assert seek.collector == catalog.APIFY and seek in catalog.APIFY_BOARDS
    assert seek.limit == 50, "the limit it had as a scraper of its own"


def test_apify_reads_what_the_seek_actors_return():
    """The field names two SEEK actors on the Apify Store document."""
    items = [
        {"title": "Senior Mining Engineer", "company": "Fortescue", "location": "Pilbara WA",
         "salary": "$180k", "postedDate": "2026-09-30", "description": "Open pit, 8/6 FIFO.",
         "url": "https://www.seek.com.au/job/11111111"},
        {"jobId": "22222222", "title": "HSE Advisor", "company": "Santos",
         "location": "Darwin NT", "jobUrl": "https://www.seek.com.au/job/22222222",
         "postedDate": "2026-10-01T00:00:00.000Z", "workType": "Full Time"},
    ]
    records = apify.parse_items(items, source_id="seek", label="SEEK", geography="AU")
    _shape_ok(records)
    assert [r["source_url"] for r in records] == [
        "https://www.seek.com.au/job/11111111", "https://www.seek.com.au/job/22222222"]
    assert [r["company"] for r in records] == ["Fortescue", "Santos"]
    assert records[0]["posted"] == "2026-09-30"
    assert all(r["source_name"] == "seek" and r["geography"] == "AU" for r in records)


def test_a_known_actor_is_told_how_many_results_in_its_own_words(panel):
    """One SEEK actor fetches 300 results unless told otherwise, whatever
    `maxItems` says. The board's limit goes into the actor's own field too."""
    panel.set_board("seek", "websift/seek-job-scraper", "", changed_by="admin@example.com")
    panel.set_board("jora", "shahidirfan/Jora-Jobs-Scraper", '{"keyword": "driller"}',
                    changed_by="admin@example.com")

    seek = apify._actor_input("seek", 50)
    assert seek["maxResults"] == 50 and seek["maxItems"] == 50
    assert seek["mining-resources-energy"] is True, "the default search goes with it"
    assert apify._actor_input("seek", 900)["maxResults"] == 550, "the actor accepts no more"

    jora = apify._actor_input("jora", 30)
    assert jora == {"keyword": "driller", "maxItems": 30, "results_wanted": 30}


def test_apify_skips_listings_it_cannot_address_or_name():
    items = [{"title": "No link"}, {"url": "https://x.example/1"}, "not a dict",
             {"title": "Relative link", "url": "/jobs/1"}]
    assert apify.parse_items(items, source_id="indeed", label="Indeed", geography="AU") == []


def test_apify_a_board_needs_both_a_token_and_an_actor(panel):
    ok, missing = apify.configured("indeed")
    assert ok is False and "token" in missing

    panel.set_apify_token(TOKEN, changed_by="admin@example.com")
    ok, missing = apify.configured("indeed")
    assert ok is False and "actor" in missing

    panel.set_board("indeed", "someone/indeed-scraper", "", changed_by="admin@example.com")
    assert apify.configured("indeed") == (True, None)
    assert apify.configured("jora")[0] is False, "one board's actor is not another's"


def test_apify_environment_variables_configure_nothing(panel, monkeypatch):
    """APIFY_TOKEN, APIFY_ACTORS and APIFY_INPUTS used to be a fallback."""
    monkeypatch.setenv("APIFY_TOKEN", TOKEN)
    monkeypatch.setenv("APIFY_ACTORS", "indeed=someone/indeed-scraper")
    monkeypatch.setenv("APIFY_INPUTS", '{"indeed": {"position": "mining"}}')
    panel.forget()
    assert apify.configured("indeed")[0] is False


def test_apify_an_unconfigured_board_returns_nothing_without_calling_out(panel, monkeypatch):
    def _no_network(*_a, **_k):
        raise AssertionError("an unconfigured board must not make a request")

    monkeypatch.setattr(apify.requests, "post", _no_network)
    assert asyncio.run(apify.scrape_async(INDEED, limit=5)) == []


def test_apify_sends_the_token_in_a_header_and_the_limit_as_max_items(panel, monkeypatch):
    panel.set_apify_token(TOKEN, changed_by="admin@example.com")
    panel.set_board("indeed", "someone/indeed-scraper",
                    '{"position": "mining", "maxItems": 999}', changed_by="admin@example.com")
    panel.set_board("jora", "someone/jora-scraper", '{"q": "x"}', changed_by="admin@example.com")
    seen = {}

    class _Res:
        def raise_for_status(self): pass
        def json(self): return [{"title": "Driller", "url": "https://au.indeed.com/viewjob?jk=9"}]

    def _post(url, **kw):
        seen.update(url=url, **kw)
        return _Res()

    monkeypatch.setattr(apify.requests, "post", _post)
    records = asyncio.run(apify.scrape_async(INDEED, limit=7))

    assert [r["title"] for r in records] == ["Driller"]
    assert "someone~indeed-scraper" in seen["url"]
    assert TOKEN not in seen["url"], "the token must never be in the address"
    assert seen["headers"]["Authorization"] == f"Bearer {TOKEN}"
    assert seen["json"] == {"position": "mining", "maxItems": 7}, "the run limit wins"
    # Enforced by Apify, so they hold even for an actor that ignores its input.
    assert seen["params"]["maxItems"] == 7 and seen["params"]["limit"] == 7
    assert seen["params"]["maxTotalChargeUsd"] == panel.DEFAULT_RUN_CHARGE_USD

    panel.set_apify_max_charge("0.40", changed_by="admin@example.com")
    asyncio.run(apify.scrape_async(INDEED, limit=7))
    assert seen["params"]["maxTotalChargeUsd"] == 0.40
