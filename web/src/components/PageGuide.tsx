import { useEffect, useRef } from 'react'
import { Icons } from '~/components/ui'
import type { PageGuide } from '~/lib/page-guides'

/**
 * The "how do I read this page" dialog.
 *
 * Every page used to carry its own explanations — a paragraph under a panel,
 * a "Why…" disclosure under a table. Worth having, but in the way of the
 * controls for anyone who already knows. They live here instead, one dialog per
 * page, opened from the sidebar footer.
 *
 * A native `<dialog>` opened with `showModal()`: the browser supplies the focus
 * trap, Escape to close, the inert page behind it and the backdrop, all of
 * which a hand-rolled overlay has to get right by itself.
 */
export function PageGuideDialog({ guide, open, onClose }: {
  guide: PageGuide
  open: boolean
  onClose: () => void
}) {
  const ref = useRef<HTMLDialogElement>(null)
  const bodyRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const d = ref.current
    if (!d) return
    if (open && !d.open) d.showModal()
    if (!open && d.open) d.close()
  }, [open])

  // Focus follows the jump, so a keyboard user carries on reading from the
  // section they chose rather than from the contents list.
  const jump = (id: string) => {
    const target = bodyRef.current?.querySelector<HTMLElement>(`#guide-${id}`)
    if (!target) return
    const still = window.matchMedia('(prefers-reduced-motion: reduce)').matches
    target.scrollIntoView({ block: 'start', behavior: still ? 'auto' : 'smooth' })
    target.focus({ preventScroll: true })
  }

  return (
    <dialog
      ref={ref}
      className="guide"
      aria-labelledby="guide-title"
      // Escape fires `close` on the element itself; mirrored into React state
      // so the next open is not a no-op.
      onClose={onClose}
      // A click on the backdrop lands on the dialog element; a click inside
      // lands on one of its children.
      onClick={(e) => { if (e.target === e.currentTarget) onClose() }}
    >
      {/* Rendered only while open, so a page's guide fetches nothing until
          somebody asks for it. */}
      {open && (
        <div className="guide-frame">
          <header className="guide-h">
            <div>
              <div className="kicker">Page guide</div>
              <h2 id="guide-title">{guide.title}</h2>
            </div>
            <button className="close" onClick={onClose} aria-label="Close the guide">{Icons.x}</button>
          </header>
          <div className="guide-main">
            <nav className="guide-toc" aria-label="Sections of this guide">
              <div className="guide-toc-label">On this page</div>
              {guide.sections.map((s) => (
                <button key={s.id} onClick={() => jump(s.id)}>{s.heading}</button>
              ))}
            </nav>
            <div className="guide-body" ref={bodyRef}>
              <p className="guide-lede">{guide.summary}</p>
              {guide.sections.map((s) => (
                <section key={s.id} id={`guide-${s.id}`} className="guide-section" tabIndex={-1}>
                  <h3>{s.heading}</h3>
                  {s.body}
                </section>
              ))}
            </div>
          </div>
        </div>
      )}
    </dialog>
  )
}

/** The sidebar-footer button. Collapsed to its icon on the narrow rail. */
export function PageGuideButton({ onOpen, iconOnly }: { onOpen: () => void; iconOnly: boolean }) {
  return (
    <button
      type="button"
      className="guide-btn"
      onClick={onOpen}
      aria-haspopup="dialog"
      aria-label={iconOnly ? 'Page guide' : undefined}
      title={iconOnly ? 'Page guide' : 'What each part of this page means'}
    >
      <span className="ico" aria-hidden="true">{Icons.info}</span>
      <span className="label">Page guide</span>
    </button>
  )
}
