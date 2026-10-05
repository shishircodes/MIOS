import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
import { Section, SkeletonCard } from '~/components/ui'
import {
  checkFeed,
  setAsxTickers,
  setCustomFeeds,
  sourceConfigQueryOptions,
  sourceHealthQueryOptions,
} from '~/lib/api'
import type { CustomFeed, SourceConfigStatus } from '~/lib/types'

function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-AU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

/** What two of the collectors read: the ASX companies followed, and any news
 *  feeds added beside the catalogued publications. Both are optional. */
export function SourceOptionsPanel() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(sourceConfigQueryOptions)
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)

  const [asxOpen, setAsxOpen] = useState(false)
  const [codes, setCodes] = useState('')

  const [feedOpen, setFeedOpen] = useState(false)
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [market, setMarket] = useState('AU')
  const [checked, setChecked] = useState<{ ok: boolean; detail: string } | null>(null)

  const settle = (p: SourceConfigStatus) => {
    qc.setQueryData(sourceConfigQueryOptions.queryKey, p)
    // "Custom RSS feeds" moves from Needs setup into the table on the Sources tab.
    void qc.invalidateQueries({ queryKey: sourceHealthQueryOptions.queryKey })
    setProblem(null)
    setNote(p.note ?? null)
  }
  const onError = (e: Error) => { setProblem(e.message); setNote(null) }

  const saveAsx = useMutation({
    mutationFn: setAsxTickers,
    onSuccess: (p) => { settle(p); setAsxOpen(false) },
    onError,
  })
  const saveFeeds = useMutation({
    mutationFn: setCustomFeeds,
    onSuccess: (p) => {
      settle(p)
      setFeedOpen(false); setName(''); setUrl(''); setChecked(null)
    },
    onError,
  })
  const check = useMutation({
    mutationFn: () => checkFeed(url.trim()),
    onSuccess: (r) => { setChecked(r); setProblem(null) },
    onError,
  })

  if (isPending) return <SkeletonCard rows={3} />
  if (error || !data) {
    return (
      <Section title="Source options">
        <div className="notice err">Could not load the source options. {error?.message}</div>
      </Section>
    )
  }

  const { asx, feeds } = data
  const busy = saveAsx.isPending || saveFeeds.isPending || check.isPending
  const full = feeds.feeds.length >= feeds.max
  const add: CustomFeed = { name: name.trim(), url: url.trim(), market }

  return (
    <Section title="Source options" tools={<span>OPTIONAL</span>}>
      {problem && <div className="notice err" role="alert">{problem}</div>}
      {!problem && note && <div className="notice ok" role="status">{note}</div>}

      {/* ASX companies */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose">ASX companies to follow</div>
            <div className="llm-meta">
              {asx.custom
                ? <>Your list of {asx.tickers.length} · by {asx.changedBy} on {when(asx.changedAt)}</>
                : <>The built-in list of {asx.tickers.length} miners, energy producers and contractors</>}
            </div>
          </div>
          <div className="key-actions">
            {asx.custom && (
              <button className="btn sm ghost" disabled={busy} onClick={() => saveAsx.mutate('')}>
                Use built-in list
              </button>
            )}
            <button
              className="btn sm"
              disabled={busy}
              onClick={() => { setCodes(asx.tickers.join(' ')); setAsxOpen((o) => !o) }}
            >
              {asxOpen ? 'Cancel' : 'Edit list'}
            </button>
          </div>
        </div>
        {!asxOpen && (
          <div className="ticker-list" aria-label="ASX codes followed">
            {asx.tickers.map((t) => <span key={t} className="ticker">{t}</span>)}
          </div>
        )}
        {asxOpen && (
          <form
            className="key-form opt-form"
            onSubmit={(e) => { e.preventDefault(); saveAsx.mutate(codes) }}
          >
            <label className="key-label" htmlFor="asx-codes">
              ASX codes, separated by spaces or commas · up to {asx.max}
            </label>
            <textarea
              id="asx-codes" className="key-input apify-json" rows={3} value={codes}
              spellCheck={false} placeholder="BHP RIO FMG"
              onChange={(e) => setCodes(e.target.value)}
            />
            <button className="btn sm" type="submit" disabled={busy}>
              {saveAsx.isPending ? 'Saving…' : 'Save list'}
            </button>
          </form>
        )}
      </div>

      {/* Custom feeds */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose">Custom RSS feeds</div>
            <div className="llm-meta">
              {feeds.feeds.length === 0
                ? 'None added. The catalogued publications above are read either way.'
                : <>{feeds.feeds.length} added · by {feeds.changedBy} on {when(feeds.changedAt)}</>}
            </div>
          </div>
          <div className="key-actions">
            <button
              className="btn sm"
              disabled={busy || (full && !feedOpen)}
              title={full ? `The most is ${feeds.max}` : undefined}
              onClick={() => { setChecked(null); setFeedOpen((o) => !o) }}
            >
              {feedOpen ? 'Cancel' : 'Add feed'}
            </button>
          </div>
        </div>

        {feeds.feeds.length > 0 && (
          <ul className="feed-list">
            {feeds.feeds.map((f) => (
              <li key={f.url}>
                <span className="feed-name">{f.name} <span className="muted">· {f.market}</span></span>
                <span className="mono feed-url" title={f.url}>{f.url}</span>
                <button
                  className="btn sm ghost"
                  disabled={busy}
                  aria-label={`Remove ${f.name}`}
                  onClick={() => saveFeeds.mutate(feeds.feeds.filter((x) => x.url !== f.url))}
                >
                  Remove
                </button>
              </li>
            ))}
          </ul>
        )}

        {feedOpen && (
          <form
            className="key-form gd-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (add.name && add.url) saveFeeds.mutate([...feeds.feeds, add])
            }}
          >
            <label className="key-label" htmlFor="feed-name">Publication</label>
            <input
              id="feed-name" className="key-input" value={name} autoComplete="off"
              placeholder="The National" onChange={(e) => setName(e.target.value)}
            />
            <label className="key-label" htmlFor="feed-url">Feed address</label>
            <input
              id="feed-url" className="key-input" value={url} autoComplete="off" spellCheck={false}
              placeholder="https://example.com/feed"
              onChange={(e) => { setUrl(e.target.value); setChecked(null) }}
            />
            <label className="key-label" htmlFor="feed-market">Market</label>
            <select
              id="feed-market" className="key-input opt-select" value={market}
              onChange={(e) => setMarket(e.target.value)}
            >
              {feeds.markets.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
            <div className="apify-form-actions">
              <button
                className="btn sm ghost" type="button" disabled={busy || !add.url}
                onClick={() => check.mutate()}
              >
                {check.isPending ? 'Checking…' : 'Check address'}
              </button>
              <button className="btn sm" type="submit" disabled={busy || !add.name || !add.url}>
                {saveFeeds.isPending ? 'Saving…' : 'Add feed'}
              </button>
              {checked && (
                <span className={`llm-meta${checked.ok ? '' : ' llm-warn'}`} role="status">
                  {checked.detail}
                </span>
              )}
            </div>
          </form>
        )}
      </div>
    </Section>
  )
}
