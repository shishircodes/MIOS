import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { Explainer, Section } from '~/components/ui'
import {
  clearHubSpotKey,
  fetchHubSpotProperties,
  hubspotStatusQueryOptions,
  saveHubSpotMapping,
  setHubSpotKey,
  syncHubSpot,
  watchlistQueryOptions,
} from '~/lib/api'
import type { HubSpotMapping, HubSpotProperty, HubSpotStatus, HubSpotSyncSummary } from '~/lib/types'

const KEY_SOURCE: Record<string, string> = {
  panel: 'Entered here',
  environment: 'Set on the server',
  none: 'No key',
}

function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString('en-AU', {
    day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit',
  })
}

function SyncSummary({ s, preview }: { s: HubSpotSyncSummary; preview: boolean }) {
  const unmapped = Object.entries(s.skippedUnmapped)
  return (
    <div className={`notice ${s.refused ? 'err' : preview ? '' : 'ok'} hs-summary`} role="status">
      {s.refused ? (
        <><strong>Not applied.</strong> {s.refused}</>
      ) : (
        <>
          <strong>{preview ? 'Preview — nothing changed yet.' : 'Watchlist synced.'}</strong>{' '}
          {s.total} {s.total === 1 ? 'company' : 'companies'} from {s.fetched} with a tier in HubSpot
          {' '}({['A', 'B', 'C'].map((t) => `${s.tiers[t] ?? 0} ${t}`).join(' · ')}).
          {' '}{s.added} added, {s.updated} updated, {s.removed} removed.
          {s.retagged && !preview && <> {s.retagged.changed} existing signals re-tagged.</>}
          {s.truncated && <> HubSpot stops at 10,000 results, so the list may be incomplete.</>}
        </>
      )}
      {unmapped.length > 0 && (
        <div className="hs-note">
          Left off because their tier maps to nothing:{' '}
          {unmapped.map(([v, n]) => `${n} × “${v}”`).join(', ')}.
        </div>
      )}
      {preview && s.preview && (
        <div className="hs-lists">
          {s.preview.added.length > 0 && (
            <div><b>Added</b> {s.preview.added.map((r) => `${r.company_name} (${r.tier})`).join(', ')}</div>
          )}
          {s.preview.updated.length > 0 && (
            <div><b>Updated</b> {s.preview.updated.map((r) =>
              `${r.company_name} (${r.previousTier ?? '–'}→${r.tier})`).join(', ')}</div>
          )}
          {s.preview.removed.length > 0 && (
            <div><b>Removed</b> {s.preview.removed.map((r) => r.company_name).join(', ')}</div>
          )}
        </div>
      )}
    </div>
  )
}

export function HubSpotPanel() {
  const qc = useQueryClient()
  const { data, isPending, error } = useQuery(hubspotStatusQueryOptions)
  const [keyValue, setKeyValue] = useState('')
  const [keyOpen, setKeyOpen] = useState(false)
  const [problem, setProblem] = useState<string | null>(null)
  const [note, setNote] = useState<string | null>(null)
  const [fields, setFields] = useState<HubSpotProperty[] | null>(null)
  const [draft, setDraft] = useState<HubSpotMapping | null>(null)
  const [preview, setPreview] = useState<HubSpotSyncSummary | null>(null)
  const [applied, setApplied] = useState<HubSpotSyncSummary | null>(null)

  useEffect(() => {
    if (data && !draft) setDraft(data.mapping)
  }, [data, draft])

  const settle = (payload: HubSpotStatus) => {
    qc.setQueryData(hubspotStatusQueryOptions.queryKey, payload)
    setProblem(null)
  }

  const saveKey = useMutation({
    mutationFn: (key: string) => setHubSpotKey(key),
    onSuccess: (p) => { settle(p); setNote(p.note ?? null); setKeyOpen(false) },
    onError: (e: Error) => setProblem(e.message),
  })
  const removeKey = useMutation({
    mutationFn: clearHubSpotKey,
    onSuccess: (p) => { settle(p); setNote(p.note ?? null); setFields(null) },
    onError: (e: Error) => setProblem(e.message),
  })
  const loadFields = useMutation({
    mutationFn: fetchHubSpotProperties,
    onSuccess: (r) => { setFields(r.tierCandidates); setProblem(null); setNote(null) },
    onError: (e: Error) => { setProblem(e.message); setFields(null) },
  })
  const saveMapping = useMutation({
    mutationFn: (m: HubSpotMapping) => saveHubSpotMapping(m),
    onSuccess: (p) => { settle(p); setDraft(p.mapping); setNote('Mapping saved.'); setPreview(null) },
    onError: (e: Error) => setProblem(e.message),
  })
  const runPreview = useMutation({
    mutationFn: () => syncHubSpot(true),
    onSuccess: (p) => { settle(p); setPreview(p.result ?? null); setApplied(null); setNote(null) },
    onError: (e: Error) => { setProblem(e.message); setPreview(null) },
  })
  const runSync = useMutation({
    mutationFn: () => syncHubSpot(false),
    onSuccess: (p) => {
      settle(p)
      setApplied(p.result ?? null)
      setPreview(null)
      void qc.invalidateQueries({ queryKey: watchlistQueryOptions.queryKey })
    },
    onError: (e: Error) => setProblem(e.message),
  })

  if (isPending || !draft) return null
  if (error) {
    return (
      <Section title="HubSpot watchlist">
        <div className="notice err">Could not load the HubSpot settings. {error.message}</div>
      </Section>
    )
  }

  const busy = saveKey.isPending || removeKey.isPending || loadFields.isPending
    || saveMapping.isPending || runPreview.isPending || runSync.isPending
  const hasKey = data.key.source !== 'none'
  const tierField = fields?.find((f) => f.name === draft.tierProperty)
  const industryOptions = fields ?? []
  const dirty = JSON.stringify({ ...draft, changedBy: undefined, changedAt: undefined })
    !== JSON.stringify({ ...data.mapping, changedBy: undefined, changedAt: undefined })

  return (
    <Section
      title="HubSpot watchlist"
      tools={<span>{data.lastSync ? `SYNCED ${when(data.lastSync.at).toUpperCase()}` : 'NOT SYNCED'}</span>}
    >
      {problem && <div className="notice err" role="alert">{problem}</div>}
      {note && !problem && <div className="notice ok" role="status">{note}</div>}

      {/* Key */}
      <div className="key-row">
        <div className="key-head">
          <div>
            <div className="llm-purpose">Service key</div>
            <div className="llm-meta">
              {KEY_SOURCE[data.key.source] ?? data.key.source}
              {data.key.hint && <> · ends <span className="mono">…{data.key.hint}</span></>}
              {' · '}read-only: companies and their fields
            </div>
            {data.key.unreadable && (
              <div className="llm-meta llm-warn">
                A key is stored but will not decrypt with this server&rsquo;s MIOS_CREDENTIAL_KEY.
                Enter it again.
              </div>
            )}
            {!data.canStoreKey && data.key.source === 'none' && (
              <div className="llm-meta llm-warn">
                Keys cannot be stored here until the server has MIOS_CREDENTIAL_KEY. Setting{' '}
                {data.keyEnv} on the server works too.
              </div>
            )}
          </div>
          <div className="key-actions">
            {hasKey && (
              <button className="btn sm ghost" disabled={busy} onClick={() => loadFields.mutate()}>
                {loadFields.isPending ? 'Checking…' : fields ? 'Reload fields' : 'Check key & load fields'}
              </button>
            )}
            {data.key.source === 'panel' && (
              <button className="btn sm ghost" disabled={busy} onClick={() => removeKey.mutate()}>Remove</button>
            )}
            {data.canStoreKey && (
              <button className="btn sm" disabled={busy} onClick={() => setKeyOpen((o) => !o)}>
                {keyOpen ? 'Cancel' : data.key.source === 'panel' ? 'Replace' : 'Add key'}
              </button>
            )}
          </div>
        </div>
        {keyOpen && data.canStoreKey && (
          <form
            className="key-form"
            onSubmit={(e) => {
              e.preventDefault()
              if (!keyValue.trim()) return
              saveKey.mutate(keyValue.trim())
              setKeyValue('')
            }}
          >
            <label className="key-label" htmlFor="hubspot-key">HubSpot service key</label>
            <input
              id="hubspot-key" type="password" className="key-input" value={keyValue}
              autoComplete="off" spellCheck={false} placeholder="Paste the service key"
              onChange={(e) => setKeyValue(e.target.value)}
            />
            <button className="btn sm" type="submit" disabled={busy || !keyValue.trim()}>Save</button>
          </form>
        )}
      </div>

      {/* Mapping */}
      <div className="llm-row">
        <div>
          <div className="llm-purpose">Which HubSpot field is the tier</div>
          <div className="llm-needs">
            Companies with a value in this field are the watchlist. Map each value to a tier, or
            leave it blank to keep those companies off.
          </div>
          {!fields && (
            <div className="llm-meta">
              Using <span className="mono">{draft.tierProperty}</span>
              {' '}({Object.entries(draft.tierMap).map(([v, t]) => `${v}→${t}`).join(', ')}).
              {hasKey ? ' Load the fields to change it.' : ' Add the key to change it.'}
            </div>
          )}
          {fields && (
            <div className="hs-map">
              <label>
                <span className="fld">Tier field</span>
                <select
                  value={draft.tierProperty}
                  onChange={(e) => setDraft({ ...draft, tierProperty: e.target.value, tierMap: {} })}
                >
                  {!tierField && <option value={draft.tierProperty}>{draft.tierProperty}</option>}
                  {fields.map((f) => <option key={f.name} value={f.name}>{f.label}</option>)}
                </select>
              </label>
              {tierField?.options.map((o) => (
                <label key={o.value}>
                  <span className="fld">{o.label}</span>
                  <select
                    value={draft.tierMap[o.value] ?? ''}
                    onChange={(e) => setDraft({ ...draft, tierMap: { ...draft.tierMap, [o.value]: e.target.value } })}
                  >
                    <option value="">Leave off</option>
                    <option value="A">Tier A</option>
                    <option value="B">Tier B</option>
                    <option value="C">Tier C</option>
                  </select>
                </label>
              ))}
              <label>
                <span className="fld">Industry field (for sector)</span>
                <select
                  value={draft.industryProperty ?? ''}
                  onChange={(e) => setDraft({ ...draft, industryProperty: e.target.value || null })}
                >
                  <option value="">None</option>
                  {!industryOptions.some((f) => f.name === draft.industryProperty) && draft.industryProperty && (
                    <option value={draft.industryProperty}>{draft.industryProperty}</option>
                  )}
                  {industryOptions.map((f) => <option key={f.name} value={f.name}>{f.label}</option>)}
                </select>
              </label>
            </div>
          )}
          <label className="sched-toggle hs-auto">
            <input
              type="checkbox" checked={draft.autoSync}
              onChange={(e) => setDraft({ ...draft, autoSync: e.target.checked })}
            />
            <span>Sync again before each pipeline run (after the first sync done here)</span>
          </label>
        </div>
        <div className="llm-pick">
          <button className="btn sm" disabled={busy || !dirty} onClick={() => saveMapping.mutate(draft)}>
            Save mapping
          </button>
        </div>
      </div>

      {/* Sync */}
      <div className="llm-row">
        <div>
          <div className="llm-purpose">Sync</div>
          <div className="llm-needs">
            Watchlist now: {data.watchlist.fromHubspot} from HubSpot, {data.watchlist.fromSeed} from
            the built-in list. Preview first — nothing changes until you apply it.
          </div>
          {preview && <SyncSummary s={preview} preview />}
          {applied && <SyncSummary s={applied} preview={false} />}
        </div>
        <div className="llm-pick">
          <button className="btn sm ghost" disabled={busy || !hasKey || dirty} onClick={() => runPreview.mutate()}>
            {runPreview.isPending ? 'Reading HubSpot…' : 'Preview'}
          </button>
          <button
            className="btn sm" disabled={busy || !preview || !!preview.refused}
            onClick={() => runSync.mutate()}
          >
            {runSync.isPending ? 'Syncing…' : 'Apply sync'}
          </button>
        </div>
      </div>

      <Explainer title="What syncing does">
        <p>
          MIOS reads the companies that have a tier in your HubSpot and makes them the watchlist.
          Classification, the weekly digest, Mode Push and the dashboard all use the watchlist, so
          every mode then works from the client&rsquo;s own tiers. HubSpot is only read, never changed.
        </p>
        <p>
          A company already on the watchlist keeps its name and its aliases — HubSpot has no
          aliases, and they are how an advert for &ldquo;BHP Group Limited&rdquo; is matched to
          &ldquo;BHP&rdquo;. After a sync, companies HubSpot no longer tiers come off the watchlist,
          the built-in list stops being applied, and signals already collected are re-tagged
          against the new tiers without any AI calls.
        </p>
        <p>
          A sync that would leave the watchlist empty is refused, because that almost always means
          the wrong field was chosen.
        </p>
      </Explainer>
    </Section>
  )
}
