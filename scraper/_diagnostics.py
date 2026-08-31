"""Why a crawl came back empty.

`crawler.run()` reports how many requests failed, and crawlee can hand us the
exception behind each one. Both were being discarded, so a source that was
blocked, rate-limited or geo-refused looked exactly like a source that had
nothing to offer: zero records, no exception, one INFO line reading
"returned 0 records".

That cost a production debugging session. The site was reachable from a
developer's machine and returned nothing from the deployed host, and the logs
could not say which of those two things had happened, let alone why.

Failure is still graceful — the brief requires a source that cannot be reached
to return `[]` rather than stop the run. What changes is that it says so.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

#: Enough to identify the cause without filling the log with one line per
#: retried request.
MAX_RECORDED_ERRORS = 3


@dataclass
class CrawlWatch:
    """Collects the reasons requests failed, and explains an empty result."""

    source: str
    errors: list[str] = field(default_factory=list)

    def attach(self, crawler: Any) -> None:
        """Record the error behind each request that exhausted its retries."""

        @crawler.failed_request_handler
        async def _on_failure(context: Any, error: Exception) -> None:  # noqa: ANN401
            if len(self.errors) < MAX_RECORDED_ERRORS:
                url = getattr(getattr(context, "request", None), "url", "?")
                # The status code is the thing that distinguishes "blocked"
                # from "broken", so it goes first when there is one.
                status = getattr(getattr(context, "http_response", None), "status_code", None)
                prefix = f"HTTP {status}" if status else type(error).__name__
                self.errors.append(f"{prefix} on {url}: {error}")

    def report(self, stats: Any, collected: int) -> None:
        """Say what happened, at a level that matches how bad it was."""
        failed = int(getattr(stats, "requests_failed", 0) or 0)
        finished = int(getattr(stats, "requests_finished", 0) or 0)

        if collected:
            # Partial success still worth noting: some pages were lost, so the
            # count is a floor rather than the whole listing.
            if failed:
                log.warning(
                    "%s: collected %d records but %d request(s) failed — the count is "
                    "incomplete. First: %s",
                    self.source, collected, failed, self.errors[0] if self.errors else "?",
                )
            return

        if failed:
            log.error(
                "%s: collected nothing and %d request(s) failed after retries. "
                "This is the source being unreachable or refusing us, not an empty "
                "listing. Reasons: %s",
                self.source, failed, "; ".join(self.errors) or "not recorded",
            )
        elif finished:
            log.warning(
                "%s: fetched %d page(s) successfully but parsed 0 records — the page "
                "loaded and the parser found nothing, which usually means the site's "
                "markup changed.",
                self.source, finished,
            )
        else:
            log.error("%s: no requests completed at all.", self.source)
