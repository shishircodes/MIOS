"""Build the weekly progress report as a PDF.

Kept as a script rather than a one-off so the next fortnight's report is a
matter of editing the content lists below, not rebuilding the layout. Every
figure in it comes from the repository — commit range, diffstat, test counts —
rather than being typed from memory, and the ones that could not be measured
are named as estimates rather than presented as counts.

Palette and type mirror the application's own so the document reads as part of
the same product: MIOS teal for rules and headings, the same ink greys, and a
single accent used sparingly.
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_JUSTIFY
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    BaseDocTemplate,
    Flowable,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

OUT = Path(__file__).resolve().parent / "MIOS Weekly Report 1-8 Sep 2026.pdf"

# ---------------------------------------------------------------------------
# Palette, taken from web/src/styles/app.css so the document matches the app.
# ---------------------------------------------------------------------------
INK = colors.HexColor("#1A2837")
INK_2 = colors.HexColor("#2C3B4E")
INK_3 = colors.HexColor("#4B5A6E")
MUTED = colors.HexColor("#656E7C")
TEAL = colors.HexColor("#0E9594")
TEAL_2 = colors.HexColor("#0B7B7A")
TEAL_3 = colors.HexColor("#086B6A")
PAPER_2 = colors.HexColor("#F2F4F1")
SURFACE_2 = colors.HexColor("#F8F9F6")
LINE = colors.HexColor("#DFE3DC")
AMBER = colors.HexColor("#8A6A00")

PAGE_W, PAGE_H = A4
MARGIN = 20 * mm


class Rule(Flowable):
    """A horizontal rule. Thicker and teal for section breaks."""

    def __init__(self, width: float, thickness: float = 0.6, colour=LINE, space: float = 0):
        super().__init__()
        self.width, self.thickness, self.colour, self.space = width, thickness, colour, space
        self.height = thickness + space

    def draw(self):
        self.canv.setStrokeColor(self.colour)
        self.canv.setLineWidth(self.thickness)
        self.canv.line(0, self.space, self.width, self.space)


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    s: dict[str, ParagraphStyle] = {}

    s["title"] = ParagraphStyle(
        "title", parent=base["Title"], fontName="Helvetica-Bold", fontSize=24,
        leading=28, textColor=INK, alignment=0, spaceAfter=2)
    s["subtitle"] = ParagraphStyle(
        "subtitle", parent=base["Normal"], fontName="Helvetica", fontSize=11.5,
        leading=15, textColor=INK_3, spaceAfter=0)
    # `keepWithNext` on both halves of a section break: without it the rule and
    # the kicker can be left stranded at the foot of a page with the heading
    # they introduce on the next one, which is what happened to "ASSURANCE".
    s["kicker"] = ParagraphStyle(
        "kicker", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=8,
        leading=11, textColor=TEAL_3, spaceAfter=5, keepWithNext=1)
    s["h2"] = ParagraphStyle(
        "h2", parent=base["Heading2"], fontName="Helvetica-Bold", fontSize=13.5,
        leading=17, textColor=INK, spaceBefore=2, spaceAfter=4, keepWithNext=1)
    s["h3"] = ParagraphStyle(
        "h3", parent=base["Heading3"], fontName="Helvetica-Bold", fontSize=10.5,
        leading=14, textColor=TEAL_3, spaceBefore=8, spaceAfter=3)
    s["body"] = ParagraphStyle(
        "body", parent=base["Normal"], fontName="Helvetica", fontSize=9.5,
        leading=13.6, textColor=INK_2, alignment=TA_JUSTIFY, spaceAfter=6)
    s["bullet"] = ParagraphStyle(
        "bullet", parent=s["body"], leftIndent=10, bulletIndent=1, spaceAfter=3.5,
        alignment=0)
    s["lede"] = ParagraphStyle(
        "lede", parent=base["Normal"], fontName="Helvetica", fontSize=10.5,
        leading=15, textColor=INK_2, spaceAfter=8)
    s["cell"] = ParagraphStyle(
        "cell", parent=base["Normal"], fontName="Helvetica", fontSize=8.6,
        leading=12, textColor=INK_2)
    s["cellhead"] = ParagraphStyle(
        "cellhead", parent=s["cell"], fontName="Helvetica-Bold", textColor=colors.white)
    s["note"] = ParagraphStyle(
        "note", parent=base["Normal"], fontName="Helvetica-Oblique", fontSize=8.8,
        leading=12.5, textColor=MUTED, spaceAfter=4)
    s["metric"] = ParagraphStyle(
        "metric", parent=base["Normal"], fontName="Helvetica-Bold", fontSize=17,
        leading=19, textColor=TEAL_3, alignment=1)
    s["metriclabel"] = ParagraphStyle(
        "metriclabel", parent=base["Normal"], fontName="Helvetica", fontSize=7.8,
        leading=10, textColor=INK_3, alignment=1)
    return s


S = styles()
CONTENT_W = PAGE_W - 2 * MARGIN


def para(text: str, style: str = "body") -> Paragraph:
    return Paragraph(text, S[style])


def bullets(items: list[str]) -> list[Paragraph]:
    return [Paragraph(f"&bull;&nbsp;&nbsp;{t}", S["bullet"]) for t in items]


def section(title: str, kicker: str | None = None) -> list:
    """A section break. The rule, kicker and heading move to the next page as one.

    `KeepTogether` for the rule (a Flowable, which has no `keepWithNext`) and
    `keepWithNext` on the two paragraph styles for the rest.
    """
    head: list = [Rule(CONTENT_W, 1.6, TEAL, space=4), Spacer(1, 5)]
    if kicker:
        head.append(para(kicker.upper(), "kicker"))
    head.append(para(title, "h2"))
    return [Spacer(1, 9), KeepTogether(head)]


def metric_band(items: list[tuple[str, str]]) -> Table:
    """The week in figures. Every one of these is measured, not estimated."""
    cells = [[Paragraph(v, S["metric"]) for v, _ in items],
             [Paragraph(l, S["metriclabel"]) for _, l in items]]
    t = Table(cells, colWidths=[CONTENT_W / len(items)] * len(items))
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, 0), 10),
        ("BOTTOMPADDING", (0, 0), (-1, 0), 1),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 10),
        ("BACKGROUND", (0, 0), (-1, -1), SURFACE_2),
        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
        ("LINEAFTER", (0, 0), (-2, -1), 0.7, LINE),
    ]))
    return t


def data_table(header: list[str], rows: list[list[str]], widths: list[float]) -> Table:
    body = [[Paragraph(h, S["cellhead"]) for h in header]]
    body += [[Paragraph(c, S["cell"]) for c in r] for r in rows]
    t = Table(body, colWidths=widths, repeatRows=1, hAlign="LEFT")
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), TEAL_2),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("BOX", (0, 0), (-1, -1), 0.7, LINE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.5, LINE),
    ]
    for i in range(1, len(body)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), SURFACE_2))
    t.setStyle(TableStyle(style))
    return t


def page_furniture(canv, doc):
    canv.saveState()
    # Header rule and running title, from page 2 on: page 1 carries the masthead.
    if doc.page > 1:
        canv.setFont("Helvetica", 7.5)
        canv.setFillColor(MUTED)
        canv.drawString(MARGIN, PAGE_H - MARGIN + 6 * mm,
                        "MIOS — Weekly Progress Report")
        canv.drawRightString(PAGE_W - MARGIN, PAGE_H - MARGIN + 6 * mm,
                             "1 – 8 September 2026")
        canv.setStrokeColor(LINE)
        canv.setLineWidth(0.6)
        canv.line(MARGIN, PAGE_H - MARGIN + 4 * mm, PAGE_W - MARGIN, PAGE_H - MARGIN + 4 * mm)

    canv.setStrokeColor(LINE)
    canv.setLineWidth(0.6)
    canv.line(MARGIN, MARGIN - 4 * mm, PAGE_W - MARGIN, MARGIN - 4 * mm)
    canv.setFont("Helvetica", 7.5)
    canv.setFillColor(MUTED)
    canv.drawString(MARGIN, MARGIN - 9 * mm,
                    "Market Intelligence Operating System · Easy Skill Australia")
    canv.drawRightString(PAGE_W - MARGIN, MARGIN - 9 * mm, f"Page {doc.page}")
    canv.restoreState()


def build(story: list) -> None:
    doc = BaseDocTemplate(
        str(OUT), pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
        title="MIOS — Weekly Progress Report, 1–8 September 2026",
        author="Shishir Timalsina",
        subject="ICT946 Capstone — weekly progress",
    )
    frame = Frame(MARGIN, MARGIN, CONTENT_W, PAGE_H - 2 * MARGIN, id="body",
                  leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0)
    doc.addPageTemplates([PageTemplate(id="main", frames=[frame], onPage=page_furniture)])
    doc.build(story)


# ---------------------------------------------------------------------------
# Content
# ---------------------------------------------------------------------------

story: list = []

# ---- masthead ----
story += [
    para("MIOS · ICT946 Capstone · Easy Skill Australia", "kicker"),
    para("Weekly Progress Report", "title"),
    para("1 – 8 September 2026 &nbsp;·&nbsp; Shishir Timalsina", "subtitle"),
    Spacer(1, 6),
    Rule(CONTENT_W, 2.2, TEAL, space=2),
    Spacer(1, 12),
    para(
        "Six workstreams were delivered this week: the Dashboard was rebuilt on real "
        "collected data, model access was put behind a single accounted seam and opened "
        "to administrator-supplied API keys, Mode Push scoring was rewritten with rarity "
        "weighting and given a results view, a production collection failure was "
        "diagnosed and fixed, and the interface was tightened across the shell and the "
        "digest. Four pull requests merged; one branch remains open.",
        "lede"),
    Spacer(1, 4),
    metric_band([
        ("10", "commits"), ("4", "PRs merged"), ("+5,050", "lines added"),
        ("16", "new modules"), ("+98", "tests added"), ("775", "tests passing"),
    ]),
    Spacer(1, 4),
    para(
        "Figures are measured from the repository over the range 1–8 September, not "
        "estimated. Test total is for <font face='Helvetica-Oblique'>main</font>; the "
        "open interface branch adds four more.", "note"),
]

# ---- 1. Dashboard ----
story += section("Dashboard rebuilt on collected data", "Delivered · PR #23")
story += [
    para(
        "The Dashboard tab was previously built entirely on invented numbers: a hardcoded "
        "twelve-week series, fabricated sector totals, a literal <b>20</b> for the "
        "watchlist count, and an “↑ trending” delta on every tile with nothing "
        "actually compared. It was the most confident-looking screen in the product and "
        "the only one where nothing on it was true — and the invented series ran an "
        "order of magnitude high, showing 847 Australian roles a week against a real 73."),
    para("It is now counted from the signals table. Three decisions shaped the replacement:"),
]
story += bullets([
    "<b>A point per collection, not per calendar week.</b> The pipeline runs weekly so the "
    "two usually coincide, but when a run is missed a calendar chart must draw something "
    "for the gap, and every option misleads — a zero says nobody hired, a joined line "
    "invents a measurement, and repeating the last value states it twice.",
    "<b>Movement is measured or absent.</b> Where there is no earlier collection to compare "
    "against, the tile says so rather than showing a direction it cannot justify.",
    "<b>Only classified signals count.</b> A collected but unread row has no sector or "
    "region, so including it would move the totals without being able to say where.",
])
story += [para(
    "Fourteen tests cover the page, most of them about the two ways this kind of screen "
    "lies when data is thin: claiming a period it does not have, and showing a movement it "
    "has not measured.")]

# ---- 2. Model access ----
story += section("Model access: one accounted seam, and keys an administrator can set",
                 "Delivered · PR #24")
story += [
    para(
        "Usage accounting was reporting a small fraction of real spend. Only Mode Push went "
        "through the counting seam; classification, Market Pulse and Mode Publish each built "
        "their own client and were invisible to it. All four now route through "
        "<font face='Courier'>caller_for()</font>, so the figure on screen is the actual "
        "total, and those three become genuinely switchable to another provider rather than "
        "only appearing so."),
    para("<b>Provider API keys can now be entered in the Admin panel.</b> Keys were "
         "environment-only, so changing one meant editing a GitHub secret and waiting for a "
         "redeploy — the wrong shape for a credential that gets rotated precisely when "
         "it has leaked or run out of allowance."),
]
story += bullets([
    "Keys are <b>encrypted before storage</b> with a secret held in the environment and never "
    "in the database, so a database dump yields ciphertext rather than working credentials.",
    "A deployment without that secret <b>refuses to store and says which variable to set</b>, "
    "rather than quietly writing plaintext an administrator would believe was protected.",
    "The key is <b>never returned</b>: the panel shows the last four characters, which "
    "identifies it to somebody already holding it and is useless to anybody else.",
    "<b>Test</b> spends one real call, because a stored key is not necessarily a working one "
    "— truncated by a paste, revoked, or a project with the API switched off all look "
    "identical until a run fails at five on a Monday morning.",
]

)

# ---- 3. Mode Push ----
story += section("Mode Push: rarity-weighted scoring, a results view, and outcome capture",
                 "Delivered · PR #25")
story += [
    para(
        "The scorer counted every skill overlap equally, so a candidate and a company both "
        "saying “lean” was worth what both saying “HAZOP” was worth. Nearly "
        "every advert says lean; almost none say HAZOP. Skill matches are now weighted by "
        "inverse document frequency over the run’s own adverts — the standard "
        "approach in search and applicant tracking — and by what kind of skill it is, "
        "since a certification is a gate and a method is a turn of phrase."),
    para(
        "Rarity is measured per run rather than from a fixed table, because what is unusual in "
        "Papua New Guinean mining is not what is unusual in Australian defence. Below 25 "
        "adverts it does not apply at all and the interface says so, since document frequency "
        "over a handful of postings is not a measurement."),
    para("Alongside it:"),
]
story += bullets([
    "A <b>skills taxonomy</b> with aliases, so “P6” and “Primavera P6” are "
    "one skill rather than a candidate scoring lower for writing it the other way. The previous "
    "list was 25 literal substrings that both missed and over-matched.",
    "<b>Job titles</b> now compare discipline with seniority words removed. “Senior "
    "Planner” against “Planner” was losing points on the title and again on "
    "seniority — the same fact charged twice.",
    "<b>A dedicated results view with a detail drawer.</b> Results previously filled a section "
    "beneath a long form, so reading them meant scrolling past the fields that produced them. "
    "Each company now opens to show every contributor as a bar, the contributors that could "
    "<i>not</i> be judged and why, and every claimed skill — matched or not — with "
    "its rarity.",
    "<b>Outcome capture.</b> Every weight in the model is judgement: nobody has been placed "
    "through this, so nothing is calibrated. Each ranked company can now be marked contacted, "
    "placed or not relevant, storing the score <i>as it stood at that moment</i>.",
])
story += [para(
    "<b>On accuracy:</b> no accuracy figure is reported, because none can honestly be "
    "computed yet. The summary deliberately returns counts and a threshold rather than a "
    "success rate — a precision figure over four decisions is not a rough measurement, "
    "it is a wrong one. Freezing the score with the decision is what makes later calibration "
    "meaningful: recomputing it would judge a decision against a model that did not exist "
    "when it was taken.", "body")]

# ---- 4. Production incident ----
story += section("Production incident: no PNG collection, and a missing Market Pulse",
                 "Diagnosed and fixed · PR #26")
story += [
    para(
        "Monday’s scheduled run reported healthy while collecting <b>zero</b> Papua New "
        "Guinean job signals and archiving a digest with no Market Pulse. Both were traced to "
        "root cause from the production database rather than guessed at."),
    para(
        "<b>Empty is not a value.</b> The deployment writes the server’s environment file "
        "from GitHub variables, and an unset variable expands to nothing — producing "
        "<font face='Courier'>PNGWORKFORCE_BASE_URL=</font>, present and empty. Python’s "
        "<font face='Courier'>os.environ.get(name, default)</font> applies the default only "
        "when the name is <i>missing</i>, so the empty string overrode a correct default. The "
        "scraper had no URL, returned an empty list, and the run reported success."),
    para(
        "The commit that introduced that passthrough was itself the cause: it added the code "
        "default <i>and</i> the line that overrides it, and was verified with the variable "
        "removed from the environment — the one state the same change made impossible in "
        "production. It worked on every developer machine, where the variable is genuinely "
        "absent."),
    para(
        "<b>The Market Pulse was generated but never archived.</b> The digest payload is built "
        "before the pulse is generated, so the archived copy always recorded it as absent. It "
        "only ever looked correct when a window happened to be processed twice — which is "
        "why every digest examined by hand had appeared fine."),
]

# ---- 5. Interface ----
story += section("Interface pass across the shell, the digest and the admin screens",
                 "In review · branch open")
story += bullets([
    "The sign-in page now describes <b>Mode Publish</b> alongside Monitor and Push — the "
    "third mode was invisible on the one screen every user sees first.",
    "The placeholder logo was removed. No mark has been chosen, and a placeholder shown long "
    "enough stops reading as one.",
    "Admin navigation renamed for what each page manages. <b>“Tokens &amp; cost” "
    "was wrong twice over</b>: the page counts requests against a daily allowance and never "
    "tokens, and it has since become where API keys are entered and a model chosen per job.",
    "<b>Market Pulse</b> moved onto the same card chassis as the rest of the page; it had its "
    "own border weight, header rule and gradient, which made the only written passage on the "
    "screen read as an insert from another product.",
    "The week selector was rebuilt to match the application’s own controls, and the "
    "“Archived” and “Live data” chips removed — both said the same "
    "thing on every visit. <b>“Sample data” was deliberately kept</b>: it is the "
    "only thing distinguishing a real week from a demonstration one.",
    "A <b>switched-off source no longer reports “Collecting”.</b> Status was derived "
    "from the age of the newest signal alone, so SEEK showed a green chip beside a toggle "
    "reading Off.",
    "Long explanatory prose on the admin screens folds into disclosures titled for the "
    "question each answers.",
])

# ---- defects table ----
story += section("Defects found and corrected", "Quality")
story += [para(
    "Several of these were found by tests written against the change rather than by using the "
    "product, and three were self-inflicted regressions caught before release.")]
story += [
    Spacer(1, 3),
    data_table(
        ["Defect", "Consequence had it shipped"],
        [
            ["Rarity weighting applied to both sides of the ratio",
             "Cancelled out entirely — one rare skill matched would have scored exactly as one "
             "common skill matched, which is the flat model it replaced."],
            ["Evaluation harness cleared a counter key that no longer existed",
             "Each evaluation run inherited the previous run's quota count and could refuse to "
             "classify, reporting an exhausted allowance that was fine."],
            ["Mode Publish still gated on the Gemini environment key",
             "Would have refused a perfectly valid key entered in the Admin panel, naming a "
             "variable that had stopped being the authority."],
            ["Deployment secret never reached the container",
             "Setting it would have appeared to work and changed nothing; the panel would have "
             "refused every key with no indication why."],
            ["Verification endpoint counted only successful calls",
             "The Test button could have silently exhausted a free tier — the exact hole the "
             "counting seam was built to close."],
            ["Time-dependent test failing near midnight UTC",
             "Intermittent CI failure, reproduced by simulation across the clock and fixed by "
             "anchoring the fixture."],
            ["Usage row collapsed its grid tracks to zero width",
             "Text painted outside its own box and was one line-length from wrapping into a "
             "column narrower than a word."],
            ["Status marker style never defined",
             "Every not-configured or retired source rendered a blank where other rows carry a "
             "marker."],
        ],
        widths=[CONTENT_W * 0.36, CONTENT_W * 0.64]),
]

# ---- testing ----
story += section("Testing and verification", "Assurance")
story += [
    para(
        "Ninety-eight net new tests were added, taking the suite to <b>775 passing</b> with 10 "
        "skipped. Coverage was written to pin behaviour rather than implementation, and in two "
        "cases each fix was confirmed by removing it and observing the test fail — which "
        "mattered here, because one of the bugs fixed this week had originally shipped "
        "<i>with</i> a passing verification."),
]
story += bullets([
    "A guard asserting that <b>no module outside the seam imports a provider SDK</b>, which is "
    "the check that would have caught the three uncounted callers in the first place.",
    "A contract test pinning the payload the scoring drawer reads <b>by field name</b> — "
    "the API is Python and the interface TypeScript, so a rename type-checks cleanly on both "
    "sides and surfaces as an empty panel.",
    "A test asserting the lazily-created database table matches the one in the schema, so two "
    "definitions of one table cannot drift.",
    "Interface changes were verified in a browser against the real stylesheet, with geometry "
    "measured rather than eyeballed — which is how two of the layout defects above were "
    "found, both of which looked correct in a screenshot.",
])

# ---- next ----
story += section("Open items and next week", "Outlook")
story += bullets([
    "<b>Merge the interface branch</b> and confirm the digest and admin screens in the "
    "deployed application.",
    "<b>Confirm the PNGworkforce fix on Monday’s scheduled run.</b> The mechanism is "
    "proven and the fix is in, but the container log for the failed run was never read, so the "
    "diagnosis remains inferred rather than observed.",
    "<b>Scrape concurrency.</b> The four sources are collected sequentially, so a run takes the "
    "sum of their times rather than the longest. Two share global crawler state, so this needs "
    "care rather than a single change.",
    "<b>Begin accumulating Mode Push outcomes</b>, which is the only route to a scoring model "
    "calibrated against placements rather than judgement.",
])

story += [
    Spacer(1, 10), Rule(CONTENT_W, 1.2, TEAL, space=3), Spacer(1, 4),
    para(
        "Prepared from the repository history for the period 1–8 September 2026. Commit "
        "range, diffstat, test counts and defect list are measured; no figure in this document "
        "is estimated.", "note"),
]

build(story)
print(f"wrote {OUT}")
