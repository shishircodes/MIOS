"""Every data source MIOS knows about, in one place.

The Easy Skill data-sources guide (Australia & Papua New Guinea) lists about
seventy sources across nine sections. Until now the code knew six of them, as
six scattered facts: a registry here, a label table in the admin API, a feed
list inside one scraper. This is the single list those are now derived from.

A source is in the catalogue whether or not MIOS can collect from it. That is
the point: "why are we not reading Indeed?" deserves an answer on the page, and
"it needs an Apify actor nobody has configured" or "it is a paid subscription"
is a better one than silence.

Three kinds of entry:

* **Collected.** `collector` says how — an RSS feed, a purpose-built module, or
  an Apify actor. Each becomes its own source in the registry, with its own
  on/off switch and its own per-run limit.
* **Connected elsewhere.** HubSpot and Slack are wired up under Integrations;
  they are listed here so the catalogue matches the guide.
* **Not collected.** `collector` is None and `availability` says why: a paid
  subscription, a key nobody has supplied, a site that forbids crawlers, a
  document that is published as a PDF rather than a feed.

What is recorded as reachable was checked live (5 Oct 2026) with the project's
own user agent, and robots.txt was read for every site. A source whose robots
file disallows crawling is not collected, whatever the guide suggests.

Adding a source the guide gains later is one entry here. An RSS feed needs
nothing else at all.
"""
from __future__ import annotations

from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Vocabulary
# ---------------------------------------------------------------------------

#: The guide's sections, in the guide's order. (key, heading)
CATEGORIES: tuple[tuple[str, str], ...] = (
    ("jobs", "Job boards"),
    ("linkedin", "LinkedIn"),
    ("projects", "Project intelligence platforms"),
    ("news", "News & industry publications"),
    ("financial", "Financial & regulatory"),
    ("tenders", "Government & development agency tenders"),
    ("internal", "Internal systems"),
    ("ai", "AI-powered intelligence tools"),
)
CATEGORY_LABEL: dict[str, str] = dict(CATEGORIES)

# How a collected source is read.
RSS = "rss"          # a feed, read by scraper.newsfeed
MODULE = "module"    # its own scraper module, named by `id`
APIFY = "apify"      # an Apify actor, run by scraper.apify

# Why a source is not collected.
SUBSCRIPTION = "subscription"  # paid; nothing to read without a licence
NEEDS_KEY = "needs_key"        # an API key or account nobody has supplied
BLOCKED = "blocked"            # the site forbids or refuses automated readers
UNREACHABLE = "unreachable"    # did not answer when checked
MANUAL = "manual"              # published as documents, not as a feed
CONNECTED = "connected"        # wired up elsewhere in MIOS (Integrations)
PLANNED = "planned"            # in the guide for a later phase

AVAILABILITY_LABEL: dict[str, str] = {
    SUBSCRIPTION: "Subscription required",
    NEEDS_KEY: "Needs an account or key",
    BLOCKED: "Does not allow automated access",
    UNREACHABLE: "Unreachable",
    MANUAL: "Published as documents",
    CONNECTED: "Connected under Integrations",
    PLANNED: "Planned",
}


@dataclass(frozen=True)
class Source:
    """One source from the guide."""

    id: str
    label: str
    category: str
    #: The sub-heading it sits under in the guide ("Australia", "Mining & Resources").
    group: str
    market: str
    sectors: str
    #: What it provides, in a phrase.
    provides: str
    #: How it is (or would be) read, as the guide puts it.
    access: str
    cost: str = "Free"
    #: The guide's Month 1 priority, where it gives one.
    priority: str | None = None
    url: str = ""

    # --- collected sources ---
    collector: str | None = None
    feed_url: str | None = None
    #: The market its records belong to, known from the masthead.
    geography: str = "AU"
    source_type: str = "news"
    default_enabled: bool = True
    #: Why it ships switched off, when it does. Shown beside the switch.
    off_reason: str | None = None
    #: Records taken per run unless an administrator sets another number. The
    #: original sources keep 50; a feed carries ten or twenty articles, so the
    #: newer ones start lower — every record collected is an AI call's worth of
    #: classification, and the daily allowance is shared by all of them.
    limit: int = 50

    # --- sources that are not collected ---
    availability: str | None = None
    #: One sentence on what stands in the way.
    note: str | None = None

    @property
    def collectable(self) -> bool:
        return self.collector is not None


_APIFY_OFF = (
    "The guide names an Apify actor as the way to read this board. It runs only "
    "once an Apify token is set (APIFY_TOKEN) and an actor is named for it "
    "(APIFY_ACTORS), so it ships switched off."
)


def _apify(id: str, label: str, group: str, market: str, sectors: str, provides: str,
           url: str, *, geography: str, priority: str | None = None) -> Source:
    return Source(
        id=id, label=label, category="jobs", group=group, market=market, sectors=sectors,
        provides=provides, access="Apify actor", cost="$ (Apify usage)", priority=priority,
        url=url, collector=APIFY, geography=geography, source_type="job_board",
        default_enabled=False, off_reason=_APIFY_OFF, limit=30,
    )


def _feed(id: str, label: str, group: str, market: str, sectors: str, feed_url: str, *,
          geography: str, provides: str = "Industry news and project coverage",
          priority: str | None = None, default_enabled: bool = True,
          off_reason: str | None = None, url: str = "", limit: int = 20,
          category: str = "news") -> Source:
    return Source(
        id=id, label=label, category=category, group=group, market=market, sectors=sectors,
        provides=provides, access="RSS", priority=priority, url=url or feed_url,
        collector=RSS, feed_url=feed_url, geography=geography, source_type="news",
        default_enabled=default_enabled, off_reason=off_reason, limit=limit,
    )


# ---------------------------------------------------------------------------
# The catalogue
# ---------------------------------------------------------------------------

SOURCES: tuple[Source, ...] = (
    # ===================== 1. Job boards =====================
    Source(
        id="seek", label="SEEK", category="jobs", group="Australia", market="AU",
        sectors="All sectors", provides="Job postings, company names, role titles, locations",
        access="HTML scrape", priority="Must-have", url="https://www.seek.com.au",
        collector=MODULE, geography="AU", source_type="job_board", default_enabled=False,
        off_reason=(
            "SEEK returns HTTP 403 to this server's IP address, at their edge, "
            "before the request reaches the site. Every request fails within "
            "milliseconds however it is disguised - plain requests, browser "
            "headers, and both Chrome and Firefox impersonation were all refused "
            "identically - so this is a block on where MIOS is hosted, not on how "
            "it asks. It is not a robots.txt matter: the category pages this "
            "scraper reads are permitted, and the paths robots.txt disallows are "
            "already refused by the scraper itself. Adzuna covers the same "
            "Australian market through a licensed API and is unaffected. Turning "
            "SEEK on only helps if MIOS has moved to a network SEEK does not "
            "block; otherwise it collects nothing and only makes each run slower."
        ),
    ),
    Source(
        id="adzuna", label="Adzuna", category="jobs", group="Australia", market="AU",
        sectors="All sectors", provides="Job postings aggregated across Australian boards",
        access="JSON API (licensed)", url="https://www.adzuna.com.au",
        collector=MODULE, geography="AU", source_type="job_board",
    ),
    _apify("indeed", "Indeed Australia", "Australia", "AU", "All sectors",
           "Job postings aggregated from multiple sources", "https://au.indeed.com",
           geography="AU", priority="Must-have"),
    _apify("jora", "Jora", "Australia", "AU", "All sectors",
           "Aggregator pulling from company sites and smaller boards", "https://au.jora.com",
           geography="AU"),
    _apify("linkedinjobs", "LinkedIn Jobs", "Australia", "AU + PNG", "All sectors",
           "Job postings and company pages", "https://www.linkedin.com/jobs",
           geography="AU", priority="Must-have"),
    _apify("glassdoor", "Glassdoor", "Australia", "AU", "All sectors",
           "Job postings, company reviews and salary data", "https://www.glassdoor.com.au",
           geography="AU"),
    Source(
        id="miningpeople", label="Mining People International", category="jobs",
        group="Australia", market="AU", sectors="Mining",
        provides="Specialist mining job board", access="HTML scrape",
        url="https://www.mpirecruitment.au/Job/Results",
        collector=MODULE, geography="AU", source_type="job_board", limit=20,
    ),
    _apify("oilandgasjobsearch", "Oil & Gas Job Search", "Australia", "AU + PNG", "Oil & gas",
           "Specialist oil and gas board with regional coverage",
           "https://www.oilandgasjobsearch.com", geography="AU"),
    _apify("iminco", "iMINCO", "Australia", "AU", "Mining, Construction",
           "Mining and resources jobs, FIFO-focused", "https://iminco.net", geography="AU"),
    Source(
        id="workforceaustralia", label="Workforce Australia", category="jobs",
        group="Australia", market="AU", sectors="All sectors",
        provides="Government job board, including regional and remote roles",
        access="API (registration)", url="https://www.workforceaustralia.gov.au",
        availability=BLOCKED,
        note="Its robots.txt disallows all crawling; reading it needs the registered API "
             "access the guide mentions, which has not been applied for.",
    ),
    Source(
        id="pngworkforce", label="PNGworkforce", category="jobs", group="Papua New Guinea",
        market="PNG", sectors="All sectors", provides="PNG's main job board",
        access="HTML scrape", priority="Must-have", url="https://www.pngworkforce.com",
        collector=MODULE, geography="PNG", source_type="job_board",
    ),
    _apify("pngrecruitment", "PNG Recruitment", "Papua New Guinea", "PNG", "All sectors",
           "Secondary PNG job board", "https://www.pngrecruitment.com", geography="PNG"),
    _apify("pngjobfinder", "PNG Job Finder", "Papua New Guinea", "PNG", "All sectors",
           "Smaller board covering local employers", "https://www.pngjobfinder.com",
           geography="PNG"),
    Source(
        id="careerspages", label="Client careers pages", category="jobs",
        group="Papua New Guinea", market="AU + PNG", sectors="Mining, Oil & gas",
        provides="Direct careers pages of Newmont, Barrick, ExxonMobil PNG, TotalEnergies PNG",
        access="Custom actor per site", priority="Should-have",
        availability=PLANNED,
        note="Each employer's site needs its own reader; none has been built yet.",
    ),

    # ===================== 2. LinkedIn =====================
    Source(
        id="linkedin_postings", label="Company job postings", category="linkedin",
        group="LinkedIn", market="AU + PNG", sectors="All sectors",
        provides="Which watchlist companies are hiring",
        access="LinkedIn API / PhantomBuster", cost="Licence held", priority="Must-have",
        url="https://www.linkedin.com", availability=NEEDS_KEY,
        note="Needs LinkedIn API access or a PhantomBuster account. LinkedIn forbids "
             "unlicensed scraping, so MIOS does not read it directly.",
    ),
    Source(
        id="linkedin_movements", label="Employee movements", category="linkedin",
        group="LinkedIn", market="AU + PNG", sectors="All sectors",
        provides="Departures, new hires and leadership changes at watchlist companies",
        access="PhantomBuster profile scraper", cost="$$", url="https://www.linkedin.com",
        availability=NEEDS_KEY,
        note="Needs a PhantomBuster account; it also reads personal profiles, which "
             "wants a privacy decision before it is switched on.",
    ),
    Source(
        id="linkedin_pages", label="Company page activity", category="linkedin",
        group="LinkedIn", market="AU + PNG", sectors="All sectors",
        provides="News, announcements and growth signals",
        access="LinkedIn API / scraping", cost="Licence held", url="https://www.linkedin.com",
        availability=NEEDS_KEY,
        note="Needs LinkedIn API access under the current licence tier.",
    ),
    Source(
        id="linkedin_velocity", label="Job posting volume trends", category="linkedin",
        group="LinkedIn", market="AU + PNG", sectors="All sectors",
        provides="Week-over-week hiring velocity",
        access="Aggregated from job postings", url="https://www.linkedin.com",
        availability=PLANNED,
        note="Derived from LinkedIn job postings, so it follows once those are collected. "
             "MIOS already measures hiring velocity from the boards it does read.",
    ),

    # ===================== 3. Project intelligence =====================
    Source(
        id="bcigem", label="BCI GEM", category="projects", group="Mining & Resources",
        market="Global", sectors="Mining, Oil & gas, Renewables",
        provides="Planned projects from early planning to completion, with contacts",
        access="Subscription", cost="$$$", priority="Nice-to-have", url="https://www.bcigem.com",
        availability=SUBSCRIPTION, note="Paid platform; nothing can be read without a licence.",
    ),
    Source(
        id="globaldata", label="GlobalData Mining Intelligence", category="projects",
        group="Mining & Resources", market="Global", sectors="Mining",
        provides="Mine-level data, project pipelines, company profiles, deals",
        access="Subscription", cost="$$$", url="https://www.globaldata.com",
        availability=SUBSCRIPTION, note="Enterprise subscription.",
    ),
    _feed("miningtechnology", "Mining Technology", "Mining & Resources", "Global", "Mining",
          "https://www.mining-technology.com/feed/", geography="AU", category="projects",
          provides="News, project updates and company profiles",
          url="https://www.mining-technology.com", default_enabled=False,
          off_reason=(
              "Its coverage is global: the headlines are as often about American or "
              "Canadian projects as Australian ones, which is noise for a business "
              "recruiting into Australia and PNG. Switch it on if that changes."
          )),
    Source(
        id="geomechanics", label="Geomechanics.io", category="projects",
        group="Mining & Resources", market="AU", sectors="Mining",
        provides="Mining project news and the Australian project pipeline",
        access="Web", url="https://www.geomechanics.io",
        availability=MANUAL, note="Publishes no feed, and its data pages sit behind an app.",
    ),
    Source(
        id="ultradynamics", label="Ultra-Dynamics Mining Monitor", category="projects",
        group="Mining & Resources", market="AU", sectors="Mining",
        provides="Bi-monthly report on major Australian mining projects",
        access="Subscription", cost="$$", url="https://www.ultradynamics.com.au",
        availability=SUBSCRIPTION, note="Paid report.",
    ),
    Source(
        id="geoscienceaustralia", label="Geoscience Australia", category="projects",
        group="Mining & Resources", market="AU", sectors="Mining",
        provides="Pre-competitive exploration data and mineral prospectivity",
        access="Data downloads", url="https://www.ga.gov.au",
        availability=MANUAL,
        note="Exploration-stage datasets, not a feed of events; the guide rates it low priority.",
    ),
    Source(
        id="disrprojects", label="Australia Mineral Projects Database (DISR)",
        category="projects", group="Mining & Resources", market="AU", sectors="Mining, Energy",
        provides="Government database of 430+ major resource projects by stage",
        access="Annual publication", priority="Should-have", url="https://www.industry.gov.au",
        availability=MANUAL,
        note="Published once a year as a report and spreadsheet; industry.gov.au also "
             "did not answer automated requests when checked.",
    ),
    Source(
        id="epcintel", label="EPCIntel", category="projects", group="Oil & Gas",
        market="Global", sectors="Energy, Oil & gas, Chemicals",
        provides="EPC/FEED contract awards and project tracking",
        access="Subscription", cost="$", priority="Should-have", url="https://www.epcintel.com",
        availability=SUBSCRIPTION, note="Enterprise subscription.",
    ),
    Source(
        id="upstream", label="Upstream Online", category="projects", group="Oil & Gas",
        market="Global", sectors="Oil & gas",
        provides="Project news, contract awards and company activity",
        access="Subscription", cost="$$", url="https://www.upstreamonline.com",
        availability=SUBSCRIPTION, note="Articles sit behind a paid sign-in.",
    ),
    Source(
        id="appea", label="Australian Energy Producers (APPEA)", category="projects",
        group="Oil & Gas", market="AU", sectors="Oil & gas",
        provides="Industry news and regulatory developments",
        access="Web", cost="Free (news) / $$ (data)", url="https://energyproducers.au",
        availability=MANUAL, note="Publishes no feed.",
    ),
    Source(
        id="bciaustralia", label="BCI Australia", category="projects",
        group="Construction & Infrastructure", market="AU", sectors="Construction",
        provides="Construction project leads, early-stage to completion",
        access="Subscription", cost="$$", url="https://www.bciaustralia.com.au",
        availability=SUBSCRIPTION, note="Paid platform.",
    ),
    Source(
        id="infrastructurepipeline", label="Australian Government Infrastructure Pipeline",
        category="projects", group="Construction & Infrastructure", market="AU",
        sectors="Construction, Infrastructure",
        provides="Government infrastructure project pipeline",
        access="Web", priority="Should-have", url="https://infrastructurepipeline.org",
        availability=MANUAL,
        note="A searchable site with no feed or API, and its robots.txt disallows the data files.",
    ),
    Source(
        id="chemxplore", label="chemXplore", category="projects",
        group="Chemical / Process Industries", market="Global", sectors="Chemicals, Process",
        provides="Chemical industry project tracking",
        access="Subscription", cost="Free (basic) / $$ (full)", priority="Should-have",
        url="https://www.chemxplore.com", availability=SUBSCRIPTION,
        note="Project data sits behind an account.",
    ),
    Source(
        id="globalenergymonitor", label="Global Energy Monitor", category="projects",
        group="Cross-Sector / Global", market="Global", sectors="Energy, Mining",
        provides="Open trackers for coal mines, oil and gas extraction, gas infrastructure",
        access="Spreadsheet downloads", priority="Should-have",
        url="https://globalenergymonitor.org", availability=MANUAL,
        note="The trackers are spreadsheets released behind a request form, not a feed.",
    ),
    Source(
        id="blackridge", label="Blackridge Research", category="projects",
        group="Cross-Sector / Global", market="Global", sectors="Oil & gas",
        provides="EPC market reports and project databases",
        access="Reports", cost="$$", url="https://www.blackridgeresearch.com",
        availability=SUBSCRIPTION, note="Paid reports.",
    ),

    # ===================== 4. News & industry publications =====================
    _feed("australianmining", "Australian Mining", "Australia", "AU", "Mining",
          "https://www.australianmining.com.au/feed/", geography="AU", priority="Must-have"),
    _feed("miningcomau", "Mining.com.au", "Australia", "AU", "Mining",
          "https://mining.com.au/feed/", geography="AU", priority="Must-have"),
    _feed("miningmonthly", "Mining Monthly", "Australia", "AU", "Mining",
          "https://www.miningmonthly.com/feeds/rss", geography="AU"),
    Source(
        id="afr", label="The Australian Financial Review", category="news", group="Australia",
        market="AU", sectors="All sectors (business/finance)",
        provides="ASX and company news", access="Subscription", cost="$$",
        url="https://www.afr.com", availability=SUBSCRIPTION,
        note="Paid subscription, and its public feeds no longer answer.",
    ),
    _feed("ausresources", "Australian Resources & Investment", "Australia", "AU",
          "Mining, Energy", "https://www.australianresourcesandinvestment.com.au/feed/",
          geography="AU"),
    _feed("energymagazine", "Energy Magazine", "Australia", "AU",
          "Energy, Oil & gas, Renewables", "https://www.energymagazine.com.au/feed/",
          geography="AU"),
    _feed("infrastructuremagazine", "Infrastructure Magazine", "Australia", "AU",
          "Construction, Infrastructure", "https://infrastructuremagazine.com.au/feed/",
          geography="AU"),
    _feed("roadsinfrastructure", "Roads & Infrastructure", "Australia", "AU", "Construction",
          "https://roadsonline.com.au/feed/", geography="AU"),
    _feed("defenceconnect", "Defence Connect", "Australia", "AU", "Defence",
          "https://www.defenceconnect.com.au/industry?format=feed&type=rss", geography="AU",
          priority="Nice-to-have", url="https://www.defenceconnect.com.au"),
    _feed("businessadvantagepng", "Business Advantage PNG", "Papua New Guinea", "PNG",
          "All sectors", "https://www.businessadvantagepng.com/feed/", geography="PNG",
          priority="Must-have"),
    Source(
        id="pngbusinessnews", label="PNG Business News", category="news",
        group="Papua New Guinea", market="PNG", sectors="All sectors",
        provides="Business news", access="HTML scrape", url="https://www.pngbusinessnews.com",
        collector=MODULE, geography="PNG", source_type="news",
    ),
    Source(
        id="thenational", label="The National (PNG)", category="news",
        group="Papua New Guinea", market="PNG", sectors="All sectors",
        provides="Newspaper", access="Web", url="https://www.thenational.com.pg",
        availability=BLOCKED, note="Its feed answers 403 to automated readers.",
    ),
    _feed("postcourier", "Post-Courier (PNG)", "Papua New Guinea", "PNG", "All sectors",
          "https://www.postcourier.com.pg/business/feed/", geography="PNG",
          provides="Newspaper business section", url="https://www.postcourier.com.pg"),
    Source(
        id="looppng", label="Loop PNG", category="news", group="Papua New Guinea",
        market="PNG", sectors="All sectors", provides="News aggregator", access="RSS",
        url="https://www.looppng.com", availability=UNREACHABLE,
        note="The site refused every connection when checked.",
    ),
    Source(
        id="pngchamber", label="PNG Chamber of Mines and Petroleum", category="news",
        group="Papua New Guinea", market="PNG", sectors="Mining, Oil & gas",
        provides="Industry body news and annual reports", access="Web",
        url="https://pngchamberminpet.com.pg", availability=UNREACHABLE,
        note="The site timed out on every request when checked.",
    ),
    Source(
        id="exepreneur", label="Exepreneur PNG", category="news", group="Papua New Guinea",
        market="PNG", sectors="All sectors", provides="Business publication with project analysis",
        access="Web", url="https://exepreneur.com", availability=UNREACHABLE,
        note="The site answered 503 (unavailable) when checked.",
    ),
    Source(
        id="newsfeed", label="Custom RSS feeds", category="news", group="Custom",
        market="AU + PNG", sectors="As configured",
        provides="Any extra feeds named in NEWS_FEEDS", access="RSS",
        collector=MODULE, geography="AU", source_type="news", default_enabled=False,
        off_reason=(
            "Reads only the feeds listed in the NEWS_FEEDS setting, as "
            "Name|https://url/feed|AU entries. With none listed it has nothing to read."
        ),
    ),

    # ===================== 5. Financial & regulatory =====================
    Source(
        id="asx", label="ASX Announcements", category="financial", group="Australia (ASX)",
        market="AU", sectors="Mining, Energy, Construction",
        provides="Company announcements, quarterly reports, project updates, capex guidance",
        access="JSON API", priority="Must-have", url="https://www.asx.com.au",
        collector=MODULE, geography="AU", source_type="news", limit=30,
    ),
    Source(
        id="asic", label="ASIC", category="financial", group="Australia (ASX)", market="AU",
        sectors="All sectors", provides="Company filings", access="API", cost="$",
        url="https://asic.gov.au", availability=PLANNED,
        note="Low priority in the guide, and company extracts are charged per search.",
    ),
    Source(
        id="gazette", label="Government Gazette", category="financial",
        group="Australia (ASX)", market="AU", sectors="Mining",
        provides="Regulatory approvals and mining leases", access="Web scraping",
        url="https://www.legislation.gov.au/gazettes", availability=PLANNED,
        note="Each state publishes its own gazette in its own format; none is read yet.",
    ),
    Source(
        id="disrquarterly", label="Resources & Energy Quarterly (DISR)", category="financial",
        group="Australia (ASX)", market="AU", sectors="Mining, Energy",
        provides="Authoritative quarterly overview", access="PDF download (quarterly)",
        priority="Should-have", url="https://www.industry.gov.au", availability=MANUAL,
        note="A quarterly PDF, read by a person rather than polled.",
    ),
    Source(
        id="pngipa", label="PNG Investment Promotion Authority", category="financial",
        group="Papua New Guinea", market="PNG", sectors="All sectors",
        provides="Business registrations and foreign investment", access="Web scraping",
        url="https://www.ipa.gov.pg", availability=MANUAL,
        note="Publishes no feed and no news listing that can be polled.",
    ),
    Source(
        id="bankpng", label="Bank of PNG", category="financial", group="Papua New Guinea",
        market="PNG", sectors="All sectors", provides="Economic reports and FX data",
        access="PDF download", url="https://www.bankpng.gov.pg", availability=BLOCKED,
        note="Answers 403 to automated readers; the reports are PDFs.",
    ),
    Source(
        id="pngchamberreports", label="PNG Chamber of Mines & Petroleum (reports)",
        category="financial", group="Papua New Guinea", market="PNG",
        sectors="Mining, Oil & gas", provides="Industry reports and member updates",
        access="Web + events", url="https://pngchamberminpet.com.pg",
        availability=UNREACHABLE, note="The site timed out on every request when checked.",
    ),
    Source(
        id="pngnri", label="PNG National Research Institute", category="financial",
        group="Papua New Guinea", market="PNG", sectors="All sectors",
        provides="Economic analysis", access="PDF download", url="https://www.pngnri.org",
        availability=MANUAL, note="Publishes PDFs and no feed.",
    ),

    # ===================== 6. Tenders =====================
    Source(
        id="austender", label="AusTender", category="tenders", group="Australia",
        market="AU", sectors="Defence, Construction, Energy",
        provides="Australian Government approaches to market", access="HTML scrape",
        url="https://www.tenders.gov.au", collector=MODULE, geography="AU",
        source_type="tender",
    ),
    Source(
        id="worldbank", label="World Bank", category="tenders", group="Development agencies",
        market="PNG", sectors="Infrastructure, Energy",
        provides="Project pipeline, procurement notices and contract awards",
        access="JSON API", priority="Nice-to-have", url="https://projects.worldbank.org",
        collector=MODULE, geography="PNG", source_type="tender", limit=20,
    ),
    Source(
        id="ted", label="EU tenders (TED)", category="tenders", group="Development agencies",
        market="PNG", sectors="Infrastructure",
        provides="EU-funded project tenders and infrastructure programs",
        access="TED API", url="https://ted.europa.eu", collector=MODULE, geography="PNG",
        source_type="tender", limit=10,
    ),
    Source(
        id="adb", label="Asian Development Bank", category="tenders",
        group="Development agencies", market="Pacific", sectors="Infrastructure",
        provides="Project pipeline, procurement notices, tender documents",
        access="API + RSS", priority="Nice-to-have", url="https://www.adb.org/projects",
        availability=BLOCKED,
        note="adb.org answers 403 to automated readers and its project feeds are gone.",
    ),
    Source(
        id="jica", label="JICA (Japan)", category="tenders", group="Development agencies",
        market="Pacific", sectors="Infrastructure",
        provides="Infrastructure projects and ODA-funded work", access="Web scraping",
        url="https://www.jica.go.jp/english", availability=MANUAL,
        note="Publishes no feed of projects for the Pacific.",
    ),
    Source(
        id="afd", label="AFD (France)", category="tenders", group="Development agencies",
        market="Pacific", sectors="Infrastructure",
        provides="Development projects and infrastructure funding", access="Open data",
        url="https://www.afd.fr", availability=BLOCKED,
        note="Its open-data site disallows automated access to the API in robots.txt.",
    ),
    Source(
        id="dfat", label="DFAT (Australia)", category="tenders", group="Development agencies",
        market="Pacific", sectors="Infrastructure",
        provides="Pacific infrastructure program and aid-funded projects",
        access="Web + RSS", url="https://www.dfat.gov.au", availability=UNREACHABLE,
        note="dfat.gov.au timed out on every automated request when checked.",
    ),

    # ===================== 7. Internal systems =====================
    Source(
        id="hubspot", label="HubSpot (CRM)", category="internal", group="Internal systems",
        market="AU + PNG", sectors="All sectors",
        provides="Company records for the watchlist and New Names detection",
        access="HubSpot API (read)", cost="Licence held", priority="Must-have",
        url="https://www.hubspot.com", availability=CONNECTED,
        note="Synced into the watchlist; set up under Integrations.",
    ),
    Source(
        id="teamtailor", label="Teamtailor (ATS)", category="internal",
        group="Internal systems", market="AU + PNG", sectors="All sectors",
        provides="Candidate conversation notes and job pipeline data",
        access="Teamtailor API (read)", cost="Licence held", priority="Must-have",
        url="https://www.teamtailor.com", availability=NEEDS_KEY,
        note="Needs a Teamtailor API key, and a decision on reading candidates' "
             "conversation notes, before a connector is built.",
    ),
    Source(
        id="slack", label="Slack", category="internal", group="Internal systems",
        market="AU + PNG", sectors="All sectors",
        provides="Weekly digest delivery", access="Incoming webhook", cost="Licence held",
        url="https://slack.com", availability=CONNECTED,
        note="The digest is posted after each run; set up under Integrations.",
    ),

    # ===================== 8. AI-powered intelligence tools =====================
    Source(
        id="perplexity", label="Perplexity API", category="ai", group="AI tools",
        market="AU + PNG", sectors="All sectors",
        provides="On-demand research queries for context enrichment", access="API",
        cost="$ (paid API)", url="https://www.perplexity.ai", availability=NEEDS_KEY,
        note="Needs a paid API key. It answers questions on demand; it is not a feed to poll.",
    ),
    Source(
        id="googlealerts", label="Google Alerts", category="ai", group="AI tools",
        market="AU + PNG", sectors="All sectors",
        provides="Email monitoring for company names or keywords", access="Email",
        url="https://www.google.com/alerts", availability=MANUAL,
        note="Delivers by email to a person's inbox; there is nothing for MIOS to poll.",
    ),
    Source(
        id="googlenews", label="Google News API", category="ai", group="AI tools",
        market="AU + PNG", sectors="All sectors",
        provides="Structured news search by company, topic and geography",
        access="SerpAPI or similar", cost="$$", url="https://serpapi.com",
        availability=NEEDS_KEY, note="Needs a paid SerpAPI (or similar) key.",
    ),
)

BY_ID: dict[str, Source] = {s.id: s for s in SOURCES}
assert len(BY_ID) == len(SOURCES), "duplicate source id in the catalogue"

#: The sources MIOS can collect from, in catalogue order. This is the registry.
COLLECTED: tuple[Source, ...] = tuple(s for s in SOURCES if s.collectable)

#: RSS publications, for the reader and for naming a stored article's publisher.
FEEDS: tuple[Source, ...] = tuple(s for s in COLLECTED if s.collector == RSS)

#: Boards read through an Apify actor.
APIFY_BOARDS: tuple[Source, ...] = tuple(s for s in COLLECTED if s.collector == APIFY)


def get(source_id: str) -> Source | None:
    return BY_ID.get(source_id)


def label_for(source_id: str | None) -> str:
    """A source's display name, falling back to the stored key for a retired one."""
    src = BY_ID.get(source_id or "")
    return src.label if src else (source_id or "")
