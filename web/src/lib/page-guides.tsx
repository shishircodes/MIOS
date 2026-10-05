import { useQuery } from '@tanstack/react-query'
import type { ReactNode } from 'react'
import { ScoringGuide } from '~/components/ScoringGuide'
import {
  llmUsageQueryOptions,
  scheduleQueryOptions,
  sourceHealthQueryOptions,
} from '~/lib/api'

/**
 * What each page means, section by section, for the Page guide dialog.
 *
 * The pages keep a one-line description where a setting needs one; the why —
 * how a figure is counted, what a switch really does, how to set a thing up —
 * is written here, once, in plain terms. Keyed by pathname, so a page without
 * an entry simply has no guide button.
 *
 * Where a guide quotes a number the server decides (a limit, a grace period, a
 * price), it reads it from the same query the page uses rather than repeating
 * it, so the guide cannot say one thing while the page does another.
 */
export interface PageGuide {
  title: string
  summary: ReactNode
  sections: { id: string; heading: string; body: ReactNode }[]
}

// ---------- live figures quoted by the guides ----------

function SourceFigures({ children }: { children: (d: { limit: number; stale: number; pending: number }) => ReactNode }) {
  const { data } = useQuery(sourceHealthQueryOptions)
  if (!data) return <p className="muted">Reading the source settings…</p>
  const pending = data.sources.reduce((n, s) => n + s.pending, 0)
  return <>{children({ limit: data.perSourceLimit, stale: data.staleAfterDays, pending })}</>
}

function OffByDefault() {
  const { data } = useQuery(sourceHealthQueryOptions)
  const off = (data?.sources ?? []).filter((s) => s.collectable && !s.defaultEnabled && s.offReason)
  if (!data) return <p className="muted">Reading the source settings…</p>
  if (off.length === 0) return <p>Every source ships switched on.</p>
  // Several sources can share one reason — every board waiting on an Apify
  // actor does — so each reason is given once, with the sources it covers.
  const byReason = new Map<string, string[]>()
  for (const s of off) byReason.set(s.offReason!, [...(byReason.get(s.offReason!) ?? []), s.label])
  return (
    <>
      <p>
        Some sources ship switched off on purpose. They show <b>Off by default</b> in the table.
        Switching one on is allowed, but read the reason first:
      </p>
      {[...byReason].map(([reason, labels]) => (
        <div key={reason} className="guide-callout">
          <b>{labels.join(', ')}.</b> {reason}
        </div>
      ))}
    </>
  )
}

function GraceHours() {
  const { data } = useQuery(scheduleQueryOptions)
  return <>{data ? data.graceHours : '…'}</>
}

function CostBasis() {
  const { data } = useQuery(llmUsageQueryOptions('30d'))
  if (!data) return <p className="muted">Reading the price list…</p>
  return (
    <>
      <p>
        Every AI call records how many tokens (chunks of text) the provider says it used. Each
        call is then priced at that model&rsquo;s published list price as of {data.pricesAsOf}.
        For Claude, re-reading cached input costs a tenth of the normal input price, and writing
        to the cache costs 1.25&times;.
      </p>
      <p>
        <b>These are estimates, not a bill.</b> Gemini&rsquo;s free tier charges nothing, so a
        deployment inside it pays nothing; the figure shows what the same use would cost on a
        paid plan. A model with no published price shows its tokens with no cost rather than a
        guess, and a failed call is counted but uses no tokens.
      </p>
      {data.trackedSince && (
        <p>
          Token counting began on {data.trackedSince.slice(0, 10)}. Calls before that were
          counted but not measured, so a range reaching further back shows less than was used.
        </p>
      )}
      <table className="cost-table cost-rates">
        <caption>Prices used, US$ per million tokens</caption>
        <thead>
          <tr><th>Model</th><th className="num">Input</th><th className="num">Output</th><th className="num">Cache read</th></tr>
        </thead>
        <tbody>
          {data.rates.map((r) => (
            <tr key={`${r.provider}:${r.model}`}>
              <td>
                {r.model}
                {r.longPromptThreshold && (
                  <span className="muted"> (higher above {r.longPromptThreshold.toLocaleString()} prompt tokens)</span>
                )}
              </td>
              <td className="num">{r.input.toFixed(2)}</td>
              <td className="num">{r.output.toFixed(2)}</td>
              <td className="num">{r.cacheRead.toFixed(3)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  )
}

// ---------- the guides ----------

const dashboard: PageGuide = {
  title: 'Dashboard',
  summary: (
    <>
      One screen that sums up the most recent collection: how many signals came in, where
      they came from, and what kind of week it was. A <b>collection</b> is everything one
      pipeline run gathered, normally once a week.
    </>
  ),
  sections: [
    {
      id: 'pickers',
      heading: 'Collection and market',
      body: (
        <ul>
          <li><b>Collection</b> picks which run the page describes. The newest is shown by default.</li>
          <li><b>Market</b> narrows every figure to Australia, Papua New Guinea, or both.</li>
          <li><b>Chart covers</b> sets how many past collections the chart draws.</li>
        </ul>
      ),
    },
    {
      id: 'headline',
      heading: 'The headline panel',
      body: (
        <>
          <p>
            The big number is how many signals the chosen collection produced, with the change
            from the collection before it. Below it is the Australia / PNG split, and how many
            signals were left out because they fall outside the sectors Easy Skill works in.
          </p>
          <p>
            The panel on the right says whether that run finished cleanly, when, and how many
            raw records it collected before the AI sorted them.
          </p>
        </>
      ),
    },
    {
      id: 'tiles',
      heading: 'The four tiles',
      body: (
        <ul>
          <li><b>Australia</b> and <b>Papua New Guinea</b>: signals in each market, compared with the previous collection.</li>
          <li><b>Watchlist seen</b>: how many companies on the watchlist showed up at all this time.</li>
          <li><b>New names</b>: companies that appeared but are not on the watchlist yet — possible new prospects.</li>
        </ul>
      ),
    },
    {
      id: 'chart',
      heading: 'Signals per collection',
      body: (
        <>
          <p>
            Each point on the chart is one collection, not one calendar week. Runs are weekly so
            the two usually match, but when a run is missed a calendar chart would have to draw
            something for the gap — a zero would suggest nobody was hiring, and a joined line
            would invent a figure nobody measured. Hover the chart, or focus it and use the
            arrow keys, to read each point.
          </p>
          <p>
            Rises and falls are always measured against the collection before. When there is no
            earlier one, the tile says so instead of showing an arrow.
          </p>
          <p>
            Only signals the AI has already read are counted. A record still waiting has no
            sector or region yet, so counting it would change the totals without being able to
            say where.
          </p>
        </>
      ),
    },
    {
      id: 'companies',
      heading: 'Most active companies',
      body: (
        <p>
          The companies with the most signals in this collection. <b>Relationship</b> shows their
          watchlist tier, or <b>New name</b> if they are not on it. Adverts where the AI could not
          tell who the employer was are left out, since nobody can act on them.
        </p>
      ),
    },
    {
      id: 'week',
      heading: 'What kind of week',
      body: (
        <>
          <p>Signals are split into three groups by what they mean for picking up the phone:</p>
          <ul>
            <li><b>Decision points</b> — a project, a leadership change or a financial event. Something moved, and there is a reason to call this week rather than next.</li>
            <li><b>Routine hiring</b> — ordinary vacancies. The company is ticking over; worth knowing, but not a reason to call by itself.</li>
            <li><b>Market context</b> — market and competitor news. Background for a conversation rather than a reason to start one.</li>
          </ul>
          <p>
            This grouping is a judgement about what usually merits a call, not a measurement. The
            Signal categories panel shows exactly what the AI recorded.
          </p>
        </>
      ),
    },
    {
      id: 'breakdowns',
      heading: 'Sectors and signal categories',
      body: (
        <p>
          How this collection divides by industry, and by the kind of signal the AI recorded
          (hiring, project, leadership and so on). Each bar shows the count and its share.
        </p>
      ),
    },
    {
      id: 'sources',
      heading: 'Where it came from',
      body: (
        <>
          <p>
            How many records each source collected. This total can be higher than the other
            panels, because it includes records the AI has not read yet — they were still
            collected, and leaving them out would make a working source look weaker than it is.
          </p>
          <p>
            A source missing from this list collected nothing, which is the quickest way to spot
            one that has quietly stopped working. Admin → Data sources has the detail.
          </p>
        </>
      ),
    },
  ],
}

const digest: PageGuide = {
  title: 'Weekly digest',
  summary: (
    <>
      The week&rsquo;s intelligence in one place: the signals worth acting on, who is hiring
      faster than usual, and companies you are not tracking yet. The same digest is posted to
      Slack after each run when that is switched on.
    </>
  ),
  sections: [
    {
      id: 'week',
      heading: 'Which week you are looking at',
      body: (
        <p>
          The heading shows the dates the digest covers. When nothing new arrived in the last
          seven days, the page shows the most recent collection instead and a yellow note says
          so. Past digests can be opened from the <b>Showing</b> picker at the top right — each
          one is exactly what a single pipeline run collected.
        </p>
      ),
    },
    {
      id: 'summary',
      heading: 'The summary strip',
      body: (
        <p>
          How many signals the collection holds and how many sources they came from, split into
          job postings and news, and into Australia and Papua New Guinea. Beside that: how many
          were ranked as key signals, how many companies are new names, and how many were left
          out because they fall outside Easy Skill&rsquo;s sectors.
        </p>
      ),
    },
    {
      id: 'pulse',
      heading: 'Market Pulse',
      body: (
        <p>
          A few short points written by the AI about the week, when there was enough to say.
          Points tagged <b>interpretation</b> are a reading of the data, not a count from it —
          treat them as a colleague&rsquo;s opinion. The section is left out entirely in a week
          that did not produce one.
        </p>
      ),
    },
    {
      id: 'key',
      heading: 'Key signals',
      body: (
        <>
          <p>
            The most useful signals, ranked and grouped by market. Use the filter box to narrow
            them by company, role or sector. Each row shows:
          </p>
          <ul>
            <li><b>Tier</b> (A, B or C) if the company is on the watchlist, or <b>New</b> if it is not.</li>
            <li>The <b>sector</b>, and the <b>review cycle</b> — how often it is worth checking again: weekly for ordinary hiring, monthly for leadership moves, projects and tenders, quarterly for bigger financial or market shifts.</li>
            <li>The <b>→ line</b>: the AI&rsquo;s one-sentence reason for how it read the item.</li>
            <li><b>conf</b>: how sure the AI was, out of 100.</li>
            <li>The source and the date it was collected.</li>
          </ul>
          <p>Click a signal to open its details, with links to the original advert or article.</p>
        </>
      ),
    },
    {
      id: 'velocity',
      heading: 'Hiring velocity',
      body: (
        <p>
          Companies ranked by how many signals they produced this week, compared with their
          average over the four weeks before. A big rise usually means a new project or
          contract — often a better moment to call than any single advert.
        </p>
      ),
    },
    {
      id: 'new',
      heading: 'New names',
      body: (
        <p>
          Companies that showed up this week but are not on the watchlist. The chip is a
          suggestion only (for example <b>Add to Tier B</b>) — nothing is added automatically.
          Review the company before adding it to HubSpot or the watchlist.
        </p>
      ),
    },
  ],
}

const feed: PageGuide = {
  title: 'Signal feed',
  summary: (
    <>
      Every signal MIOS has ever collected, newest first, 50 to a page. Use it to look
      something up; use the Weekly digest to see what matters this week.
    </>
  ),
  sections: [
    {
      id: 'count',
      heading: 'Signals collected all time',
      body: (
        <p>
          The number at the top right is everything collected since MIOS started. It does not
          change with the filters — the count above the list does.
        </p>
      ),
    },
    {
      id: 'filters',
      heading: 'Filters and search',
      body: (
        <ul>
          <li><b>Region</b>: Australia or Papua New Guinea.</li>
          <li><b>Cycle</b>: how often the item is worth re-checking — weekly, monthly or quarterly.</li>
          <li><b>Source</b>: which collector found it.</li>
          <li><b>Search</b>: matches company, role, sector and the text itself.</li>
        </ul>
      ),
    },
    {
      id: 'rows',
      heading: 'Reading a row',
      body: (
        <p>
          Each row shows the watchlist tier (or <b>New</b>), the sector, the date, the review
          cycle and the market, then the headline and the AI&rsquo;s one-line reason. <b>conf</b> is
          how sure the AI was, out of 100. Click a row for the full details and links to the
          source.
        </p>
      ),
    },
  ],
}

const push: PageGuide = {
  title: 'Candidate matching',
  summary: (
    <>
      Mode Push: give it a candidate, and it ranks the companies in the market that most look
      like they need that person right now, based on the last 30 days of signals.
    </>
  ),
  sections: [
    {
      id: 'profile',
      heading: 'Entering a candidate',
      body: (
        <>
          <p>
            Type the details in, or upload a CV (.docx or .pdf) to fill the form for you. The CV
            is read in memory and thrown away — it is never stored — so check the fields before
            going on.
          </p>
          <ul>
            <li><b>Find matches</b> searches without saving anyone. Use it to test a CV against the market first.</li>
            <li><b>Save profile and match</b> keeps the candidate under Saved profiles as well.</li>
          </ul>
        </>
      ),
    },
    {
      id: 'results',
      heading: 'Reading the results',
      body: (
        <>
          <p>
            Each row is a company with a score, the evidence behind it, and a confidence level.
            Open a row to see how every point was earned:
          </p>
          <ul>
            <li><b>What earned the score</b> — each part of the score and the evidence for it.</li>
            <li><b>What could not be judged</b> — parts left out because MIOS lacks the data. They are removed from the total rather than scored zero, so a company is not marked down for gaps in our own data.</li>
            <li><b>Skills</b> — which of the candidate&rsquo;s skills appear in the company&rsquo;s adverts. Rarer skills count for more: matching something every company asks for tells you nothing.</li>
            <li><b>Written note</b> — the AI&rsquo;s summary, fit verdict and a caveat to check. If it disagrees with the score, it is flagged.</li>
            <li><b>What did you do?</b> — record the outcome. It is stored against today&rsquo;s score so the scoring can later be checked against what actually worked. It does not change the ranking.</li>
          </ul>
        </>
      ),
    },
    {
      id: 'scoring',
      heading: 'How the score works',
      body: <ScoringGuide />,
    },
    {
      id: 'saved',
      heading: 'Saved profiles',
      body: (
        <p>
          Candidates you have kept. Open one to match it again against the latest signals, edit
          it, or remove it. The same candidates are suggested when you open a company from the
          digest or the signal feed.
        </p>
      ),
    },
  ],
}

const publish: PageGuide = {
  title: 'Quarterly reports',
  summary: (
    <>
      Mode Publish: a client-ready quarterly report built from the signals MIOS collected in
      that quarter. Every figure is counted from the data; the AI only writes the prose around
      it.
    </>
  ),
  sections: [
    {
      id: 'generate',
      heading: 'Making a draft',
      body: (
        <p>
          Pick a quarter and press <b>Generate draft</b>. The figures and most sections are
          filled in from that quarter&rsquo;s signals. The outlook is left for you to write,
          because it is a judgement the data cannot make.
        </p>
      ),
    },
    {
      id: 'review',
      heading: 'Reviewing and approving',
      body: (
        <>
          <p>
            Read each section, <b>Edit</b> it if needed, then <b>Approve section</b>. An empty
            section cannot be approved until it is written. Once every section is approved,
            <b> Approve report</b> locks it.
          </p>
          <p>Use <b>Delete this draft</b> to throw a draft away and start again.</p>
        </>
      ),
    },
    {
      id: 'export',
      heading: 'Exporting',
      body: (
        <>
          <ul>
            <li><b>Open printable</b> opens a clean page. For a PDF, use your browser&rsquo;s Print → Save as PDF.</li>
            <li><b>Download Markdown</b> gives a plain-text copy.</li>
            <li><b>Send to Google Docs</b> creates one Doc per report, once Google Docs is connected under Admin → Integrations. Sending again replaces that Doc&rsquo;s contents, so edits made inside Google Docs are lost.</li>
          </ul>
          <p>A draft that is not yet approved exports with a &ldquo;not approved&rdquo; banner.</p>
        </>
      ),
    },
  ],
}

const watchlist: PageGuide = {
  title: 'Watchlist',
  summary: (
    <>
      The companies Easy Skill cares about most. Every mode uses this list: the AI tags signals
      against it, the digest and dashboard highlight it, and Mode Push weighs it.
    </>
  ),
  sections: [
    {
      id: 'tiers',
      heading: 'The three tiers',
      body: (
        <ul>
          <li><b>Tier A · Active clients</b> — companies Easy Skill already works with.</li>
          <li><b>Tier B · Target prospects</b> — companies being pursued.</li>
          <li><b>Tier C · Market indicators</b> — companies watched because their moves say something about the market.</li>
        </ul>
      ),
    },
    {
      id: 'columns',
      heading: 'Aliases and notes',
      body: (
        <p>
          <b>Aliases</b> are other names the company appears under, so an advert for &ldquo;BHP
          Group Limited&rdquo; is still matched to &ldquo;BHP&rdquo;. <b>Note</b> is free text kept
          with the company.
        </p>
      ),
    },
    {
      id: 'origin',
      heading: 'Where the list comes from',
      body: (
        <p>
          The line under the heading says whether the list comes from HubSpot or the built-in
          starting list. Once HubSpot is connected and synced under Admin → Integrations,
          HubSpot is the source and this page follows it.
        </p>
      ),
    },
  ],
}

const schedule: PageGuide = {
  title: 'Schedule & runs',
  summary: (
    <>
      When the pipeline runs by itself, and a record of every run. A run collects from the
      sources, has the AI sort what is new, and builds the weekly digest.
    </>
  ),
  sections: [
    {
      id: 'auto',
      heading: 'Automatic run',
      body: (
        <>
          <p>
            Choose the day, time and timezone, then <b>Save</b>. Changes apply straight away — no
            redeploy needed. Untick <b>Run the pipeline automatically</b> to pause it; nothing is
            collected until it is turned back on or a run is started by hand.
          </p>
          <p>
            Every time on this page is shown in the schedule&rsquo;s own timezone, not your
            browser&rsquo;s or the server&rsquo;s, so it reads the same wherever you open it from.
          </p>
          <p>
            A run missed because the server was down is picked up when it comes back, but only
            within <GraceHours /> hours. Later than that the week is skipped rather than
            collected late, because each digest is labelled with the week it covers.
          </p>
          <p>
            If a red note says <b>Nothing is watching this schedule</b>, the server was started
            without its scheduler switched on, and no automatic run will happen until it is.
          </p>
        </>
      ),
    },
    {
      id: 'now',
      heading: 'Run now',
      body: (
        <p>
          Starts a full run immediately. It takes a few minutes. Only one run can happen at a
          time, so the button is disabled while one is in progress.
        </p>
      ),
    },
    {
      id: 'history',
      heading: 'Run history',
      body: (
        <p>
          Each past run with its result, how it started, when, how many records it collected and
          who started it. If a run left records unsorted because the AI failed, a <b>Retry</b>
          button appears on it to finish the job.
        </p>
      ),
    },
    {
      id: 'slack',
      heading: 'Slack',
      body: (
        <p>
          Whether each run posts the digest to Slack is set under Admin → Integrations.
        </p>
      ),
    },
  ],
}

const sources: PageGuide = {
  title: 'Data sources',
  summary: (
    <>
      Every source in Easy Skill&rsquo;s data-sources guide for Australia and Papua New Guinea:
      which ones MIOS collects from, how each is doing, and for the rest, what stands in the way.
    </>
  ),
  sections: [
    {
      id: 'layout',
      heading: 'How the page is organised',
      body: (
        <>
          <p>
            The page has two tabs. <b>Sources</b> lists every source; <b>Options &amp; limits</b>
            holds the settings that go with them. On the Sources tab a source is in exactly one
            of three lists:
          </p>
          <ul>
            <li><b>Sources</b>: everything that is set up, in one table, each with its switch. The buttons above the table narrow it to one category.</li>
            <li><b>Needs setup</b>: sources that can be collected but are waiting on a setting. The row says what, and its button goes to where it is entered. Once it is saved the source moves into the table above, switched on.</li>
            <li><b>Not collected</b>: the rest of the guide. Closed by default; <b>Show all</b> opens it.</li>
          </ul>
          <p>
            Under each name are the <b>sectors</b> the source covers. Hover a name to see what
            the source provides and how it is read.
          </p>
        </>
      ),
    },
    {
      id: 'collectors',
      heading: 'The Sources table',
      body: (
        <SourceFigures>
          {({ limit, stale, pending }) => (
            <>
              <p>
                One row per source. The switch on the right decides whether the next run uses
                it. The figures are counted from the collected records themselves, not from a
                separate log, so they always match what is really stored.
              </p>
              <ul>
                <li><b>Last run</b>: how many records the source returned the last day it collected. A source stops at its limit (normally {limit}), so a run sitting exactly on that number was probably cut short rather than finished.</li>
                <li><b>7 days</b> and <b>All time</b>: records collected in each period.</li>
                <li><b>Stale</b>: nothing collected for {stale} days — longer than a weekly cycle, so a normal week never triggers it.</li>
              </ul>
              {pending > 0 && (
                <p>
                  {pending.toLocaleString()} records are collected and stored but still waiting
                  for the AI to read them.
                </p>
              )}
            </>
          )}
        </SourceFigures>
      ),
    },
    {
      id: 'notcollected',
      heading: 'Sources MIOS does not collect from',
      body: (
        <>
          <p>
            These are kept so the page matches the guide, and so &ldquo;why are we not reading
            this?&rdquo; has an answer. The list is closed until you press <b>Show all</b>. Each
            row says what stands in the way:
          </p>
          <ul>
            <li><b>Subscription</b>: a paid platform. Nothing can be read without a licence.</li>
            <li><b>Needs a key</b>: needs an account or API key nobody has supplied yet.</li>
            <li><b>Blocked</b>: the site forbids crawlers in its robots.txt, or refuses them. MIOS does not work around that.</li>
            <li><b>Unreachable</b>: the site did not answer when it was checked.</li>
            <li><b>Documents only</b>: published as reports or spreadsheets for a person to read, not as a feed.</li>
            <li><b>Connected</b>: HubSpot and Slack, which are set up under Integrations.</li>
            <li><b>Planned</b>: in the guide for a later phase.</li>
          </ul>
        </>
      ),
    },
    {
      id: 'off',
      heading: 'Sources that ship switched off',
      body: <OffByDefault />,
    },
    {
      id: 'keys',
      heading: 'Needs setup',
      body: (
        <>
          <p>
            Two kinds of source wait for something to be entered. Until then they are skipped,
            and everything else runs as normal. Once it is entered, the source moves into the
            Sources table, switched on for the next run. It shows <b>No data yet</b> until that
            run has collected from it.
          </p>
          <ul>
            <li><b>Boards read through Apify</b> (SEEK, Indeed, Jora, Glassdoor, LinkedIn Jobs and others) need a token and an actor. <b>Set up</b> opens <b>Integrations › Apify job boards</b>, and its guide has the steps.</li>
            <li><b>Custom RSS feeds</b> needs at least one feed. <b>Add a feed</b> opens the <b>Options &amp; limits</b> tab.</li>
          </ul>
        </>
      ),
    },
    {
      id: 'options',
      heading: 'Source options',
      body: (
        <>
          <p>
            On the <b>Options &amp; limits</b> tab. Both are optional: nothing here needs filling
            in for a run to work.
          </p>
          <p><b>ASX companies to follow.</b> ASX Announcements reads the market announcements of a list of companies. It starts on a built-in list of miners, energy producers and contractors.</p>
          <ol>
            <li>Press <b>Edit list</b>.</li>
            <li>Type the ASX codes you want, separated by spaces or commas — for example <span className="mono">BHP RIO FMG</span>. This replaces the list, so keep the codes you still want.</li>
            <li>Press <b>Save list</b>. The next run follows the new list.</li>
          </ol>
          <p><b>Use built-in list</b> goes back to the original companies. Each company is one request per run, which is why the list stops at 60.</p>
          <p><b>Custom RSS feeds.</b> A publication that has no row of its own can be added if it has an RSS feed.</p>
          <ol>
            <li>Find the feed address on the publication&rsquo;s site. It often ends in <span className="mono">/feed</span> or <span className="mono">/rss</span>.</li>
            <li>Press <b>Add feed</b>, then enter the publication&rsquo;s name, the feed address and its market.</li>
            <li>Press <b>Check address</b>. It fetches the feed once and says how many articles it found, or why it cannot be read.</li>
            <li>Press <b>Add feed</b> to save. Its articles are collected from the next run as <b>Custom RSS feeds</b>.</li>
          </ol>
          <p>A publication that already has its own row cannot be added again — switch that row on instead.</p>
        </>
      ),
    },
    {
      id: 'limits',
      heading: 'Collection limits',
      body: (
        <>
          <p>
            A run fetches up to each source&rsquo;s limit, stores what is new, then sends the new
            records to the AI in batches to be sorted. Changing these numbers never touches
            records already collected. Only the sources that are switched on are listed; one
            that is off keeps its limit for when it is turned back on.
          </p>
          <p>
            Raising a source&rsquo;s limit collects more per run, but repeats are never stored
            twice, so the number of <i>new</i> records rarely rises as much. More records also
            means more AI calls, and when the day&rsquo;s calls run out, the rest wait for the next
            run — they are not lost.
          </p>
        </>
      ),
    },
    {
      id: 'ai',
      heading: 'Sending records to the AI',
      body: (
        <ul>
          <li><b>Records per AI call</b>: bigger batches mean fewer calls, but if the AI&rsquo;s answer runs past its length limit the whole batch fails. The limit stops at 50 on purpose: at 100 per call, 99 of 180 records were left unsorted on 14 Sep 2026.</li>
          <li><b>Characters kept per record</b>: the title, company and location come first, so a lower number mostly trims the body of the advert.</li>
          <li><b>AI calls per day</b>: shared by sorting and report writing. 20 is Gemini&rsquo;s free-tier limit; raise it on a paid plan.</li>
          <li><b>Seconds between AI calls</b>: keeps under the provider&rsquo;s per-minute limit. A paid plan usually allows this to be lower.</li>
        </ul>
      ),
    },
  ],
}

const integrations: PageGuide = {
  title: 'Integrations',
  summary: <>The outside systems MIOS talks to: Slack, HubSpot, Google Docs and Apify.</>,
  sections: [
    {
      id: 'slack',
      heading: 'Slack digest',
      body: (
        <>
          <p>
            After each run the weekly digest is posted to a Slack channel. Switching it off stops
            the post only — the digest is still built and kept in the Weekly digest archive.
          </p>
          <p>To get a webhook URL:</p>
          <ol>
            <li>In Slack, open <b>Tools › Apps</b> (or api.slack.com/apps) and create an app for your workspace, or open an existing one.</li>
            <li>Under <b>Incoming Webhooks</b>, switch them on and choose <b>Add New Webhook to Workspace</b>.</li>
            <li>Pick the channel the digest should go to and allow it.</li>
            <li>Copy the URL — it starts with <span className="mono">https://hooks.slack.com/services/</span> — add it here, and send a test message.</li>
          </ol>
          <p>
            Anyone holding the URL can post to that channel, so it is stored encrypted and never
            shown again in full. To move the digest to another channel, add that channel&rsquo;s
            webhook in place of this one.
          </p>
        </>
      ),
    },
    {
      id: 'hubspot',
      heading: 'HubSpot watchlist',
      body: (
        <>
          <p>
            MIOS reads the companies that have a tier in HubSpot and makes them the watchlist, so
            every mode works from the team&rsquo;s own tiers. HubSpot is only read, never changed.
          </p>
          <ul>
            <li><b>Tier field</b>: the HubSpot field that holds the tier. Map each of its values to Tier A, B or C, or leave one blank to keep those companies off.</li>
            <li><b>Target accounts only</b>: by default only companies ticked as Target accounts are read — HubSpot&rsquo;s mark for accounts a team is pursuing. A target account without a tier is left off unless you choose one for it.</li>
            <li><b>Preview</b> shows what a sync would add, change and remove. Nothing changes until <b>Apply sync</b>.</li>
          </ul>
          <p>
            After a sync, companies HubSpot no longer tiers come off the watchlist, and signals
            already collected are re-tagged against the new tiers with no AI calls. A company
            already on the list keeps its aliases, since HubSpot has none.
          </p>
          <p>
            A sync that would leave the watchlist empty is refused — that almost always means the
            wrong field was chosen. The automatic sync before each run never removes anyone: it
            adds and updates, and lists what it would have removed here for you to apply.
          </p>
        </>
      ),
    },
    {
      id: 'gdocs',
      heading: 'Google Docs',
      body: (
        <>
          <p>
            Quarterly reports can be sent to Google Docs from Mode Publish. Each report gets one
            Doc in a <b>MIOS Quarterly Reports</b> folder of the connected account&rsquo;s Drive, and
            sending again updates that same Doc. MIOS only sees files it creates, and never
            shares a Doc — whoever owns the Drive decides who sees it.
          </p>
          <p>Setting it up in Google Cloud:</p>
          <ol>
            <li>Open the Google Cloud project that holds MIOS&rsquo;s sign-in client (or any project you prefer) and enable the <b>Google Drive API</b> under APIs &amp; Services › Library.</li>
            <li>Under Credentials, open the OAuth client — the sign-in one is reused when no other is entered — and add the redirect URI shown on the panel to its authorised redirect URIs.</li>
            <li>On the OAuth consent screen, add the <span className="mono">…/auth/drive.file</span> scope. With an <b>Internal</b> (Workspace) app nothing else is needed. An <b>External</b> app left in Testing loses access every 7 days — publish it; this scope does not need Google&rsquo;s review.</li>
            <li>Press <b>Connect Google account</b> and sign in with the account whose Drive should hold the reports.</li>
          </ol>
        </>
      ),
    },
    {
      id: 'apify',
      heading: 'Apify job boards',
      body: (
        <>
          <p>
            Some job boards publish no feed and refuse ordinary readers. Apify is a paid service
            that runs ready-made readers, called <b>actors</b>, for those boards. This is
            optional: leave it empty and those boards are simply not read.
          </p>
          <p><b>Step 1 — add the token.</b></p>
          <ol>
            <li>Sign in at <span className="mono">console.apify.com</span>, or create an account. The free plan includes a small monthly credit.</li>
            <li>Open <b>Settings › API &amp; Integrations</b> and copy the <b>Personal API token</b>.</li>
            <li>Here, press <b>Add token</b>, paste it and save.</li>
            <li>Press <b>Test token</b>. It asks Apify whose token it is — no actor runs and nothing is charged.</li>
          </ol>
          <p><b>Step 2 — name an actor for each board you want.</b></p>
          <ol>
            <li>In the <b>Apify Store</b>, search for the board (for example &ldquo;SEEK scraper&rdquo;) and open an actor. Check its price and reviews first.</li>
            <li>Copy its name from the page address: <span className="mono">apify.com/<b>username/actor-name</b></span>.</li>
            <li>Here, press <b>Set actor</b> beside the board and paste the name.</li>
            <li>In <b>Search settings</b>, enter what that actor should search for, as JSON. Every actor has its own field names — they are listed on the actor&rsquo;s <b>Input</b> tab, which can also show the JSON to copy. Leave it empty to use the actor&rsquo;s defaults.</li>
            <li>Save. The board shows <b>Ready</b> once it has both a token and an actor.</li>
          </ol>
          <p><b>Step 3 — check the result.</b></p>
          <ol>
            <li>A ready board is read from the next run. Under <b>Data sources</b> its row changes from <i>Not configured</i> to <i>No data yet</i>, then <i>Collecting</i>.</li>
            <li>To stop reading a board, switch it off there, or remove its actor here.</li>
          </ol>
          <p>
            Each run of an actor is charged to the Apify account, so name actors only for boards
            worth the cost. Some actors have their own setting for how many results to fetch
            (for example <span className="mono">maxResults</span>); set it in Search settings near
            the board&rsquo;s limit, or the actor fetches, and charges for, more than MIOS keeps.
            The token is stored encrypted and never shown again in full.
          </p>
        </>
      ),
    },
  ],
}

const models: PageGuide = {
  title: 'AI models',
  summary: <>Which AI provider and model does each job, and the keys that pay for them.</>,
  sections: [
    {
      id: 'keys',
      heading: 'Provider API keys',
      body: (
        <>
          <p>
            A key added here is used from the very next call — no redeploy. That matters most
            when a key has leaked or its free allowance has run out.
          </p>
          <p>
            Keys are encrypted before they are saved, using a secret kept on the server and never
            in the database, so a copy of the database holds no working keys. A key is never sent
            back to this page; the last four characters are shown so you can tell which is loaded.
          </p>
          <p>
            Keys are entered here and nowhere else. With no key the app still runs: records are
            still collected and stored, and the jobs that need a model wait until one is added.
          </p>
          <p>
            <b>Test</b> makes one real call. A saved key is not always a working key — it can be
            cut short by a paste, revoked, or belong to a project with the API switched off — and
            all of those look the same here until a run fails.
          </p>
        </>
      ),
    },
    {
      id: 'jobs',
      heading: 'Model for each job',
      body: (
        <>
          <ul>
            <li><b>Signal classification</b> reads every collected record and fills in its company, sector, region and category. Speed and cost matter more than style here.</li>
            <li><b>Market Pulse</b> writes the short read on the week in the digest — the one place where writing quality is the point.</li>
            <li><b>Mode Publish reports</b> writes report prose around figures already counted; sticking to the numbers matters most.</li>
            <li><b>Mode Push rationale</b> writes notes on the top candidate matches in a single call. It never sets a score.</li>
          </ul>
          <p>
            A change applies to the next call, with no restart. A provider with no key can still
            be chosen: the choice is saved and takes effect once its key is added.
          </p>
        </>
      ),
    },
    {
      id: 'features',
      heading: 'AI features',
      body: (
        <p>
          Switching a feature off stops it calling a model from the next request. Scores,
          rankings and everything worked out without AI stay exactly the same — only the written
          notes go, and one model call per search is saved.
        </p>
      ),
    },
  ],
}

const usage: PageGuide = {
  title: 'Usage & cost',
  summary: <>How much the AI models have been used, and roughly what that would cost.</>,
  sections: [
    {
      id: 'cost',
      heading: 'Tokens and estimated cost',
      body: (
        <>
          <p>
            Pick a range (today, 7, 30 or 90 days, or all time) to see estimated cost, tokens in
            and out, and number of calls, then the breakdown by model and by job.
          </p>
          <CostBasis />
        </>
      ),
    },
    {
      id: 'allowance',
      heading: 'Today’s allowance',
      body: (
        <p>
          How many calls each provider has made today against its daily limit, which resets at
          midnight Pacific time. Every attempt counts, not just the ones that worked — a provider
          charges the allowance for a rejected request too. A counter that only counted
          successes once read zero on the very day the pipeline ran out.
        </p>
      ),
    },
    {
      id: 'daily',
      heading: 'Calls per day',
      body: <p>Calls made on each recent day, so a sudden jump is easy to spot.</p>,
    },
  ],
}

const access: PageGuide = {
  title: 'People & access',
  summary: <>Who can sign in to MIOS, and what they can see.</>,
  sections: [
    {
      id: 'roles',
      heading: 'Roles',
      body: (
        <ul>
          <li><b>Member</b>: the intelligence pages — dashboard, digest, signal feed, candidate matching, reports and watchlist.</li>
          <li><b>Administrator</b>: everything, including every Admin page.</li>
        </ul>
      ),
    },
    {
      id: 'list',
      heading: 'Who can sign in',
      body: (
        <p>
          <b>Add person</b> lets a Google account in by email, with a role. <b>How they got in</b>
          shows whether someone was added here or comes from the server settings. Rows with a
          lock come from the server&rsquo;s settings: they cannot be edited or removed here —
          that takes a configuration change and a restart.
        </p>
      ),
    },
    {
      id: 'domain',
      heading: 'The company domain rule',
      body: (
        <p>
          When a company domain is set, anyone with a Google account on that domain can sign in
          as a <b>member</b> without being listed. The domain is checked against what Google
          confirms about the account, not just the text of the address. It never grants
          administrator — administrators must be named in the list. Changing the domain means
          changing the server&rsquo;s settings.
        </p>
      ),
    },
  ],
}

export const PAGE_GUIDES: Record<string, PageGuide> = {
  '/': dashboard,
  '/monitor/digest': digest,
  '/monitor/feed': feed,
  '/push': push,
  '/publish': publish,
  '/watchlist': watchlist,
  '/schedule': schedule,
  '/sources': sources,
  '/integrations': integrations,
  '/models': models,
  '/usage': usage,
  '/access': access,
}
