# Catalog diagnostics and rate limits — Phase 1

This section describes the proposed catalog-only handling. It does not change Google Docs publishing, Google authorization, secrets, repository variables, or the GitHub daily schedule. The deployed GitHub workflow remains **8:00 AM America/Boise**, including daylight saving. The older standalone-host instructions below are separate; follow `docs/GITHUB_ACTIONS.md` for GitHub operation and the existing Google publishing guide for that integration.

## What happens after a catalog 429

The catalog transport makes one attempt per request. The first HTTP 429 immediately ends that scan: it does not retry the request, try another endpoint, change identities, or continue product-page downloads. Other catalog connection/server failures also stop after one transport attempt. Supplier and Freedom clients retain their existing retry and pacing behavior.

The next catalog eligibility is the later of the server's `Retry-After` and our local backoff:

| Consecutive rate-limited scans | Local minimum | Extra randomized delay |
| --- | --- | --- |
| First | 24 hours | 0–1 hour |
| Second | 48 hours | 0–1 hour |
| Third | 96 hours | 0–1 hour |
| Fourth and later | 7 days | Local delay capped at 7 days |

Numeric and HTTP-date `Retry-After` values are supported. Date intervals round up so the catalog never retries fractionally early. A missing, invalid, zero or past value does not remove the local minimum. A server instruction longer than seven days is honored; seven days caps only the **local** policy. Unrepresentably distant dates are held until the maximum representable date, requiring operator review rather than an early retry.

Eligibility does not trigger a separate job. The next existing scheduled run **after** that time can attempt the catalog. For example, a 24-hour cooldown plus jitter can skip the following day's 8 AM check. Manual runs during cooldown make zero catalog requests while the other independent checks and existing publishing stages can continue. Do not clear the cooldown or repeatedly dispatch runs to work around it.

Backoff streaks survive blocked/deferred/failed scans and reset only after a validated complete catalog has been imported successfully. A new additive `catalog_rate_state` table stores the streak and chosen delay; the existing `source_backoff` table stores the eligibility. Both travel inside the existing encrypted database artifact. No reseeding, database replacement, encryption-key change or new service is required. Old state restores normally and acquires the new table through the existing additive schema mechanism; its preexisting cooldown is still honored.

## Robots and request pacing

Catalog requests, including same-host redirects, are spaced at least five seconds apart. Published matching `Crawl-delay` and `Request-rate` rules can increase that interval. The latter is applied conservatively as uniform spacing (for example `1/20` means at least 20 seconds between requests). Decimal crawl delays are supported. The most specific matching user-agent groups determine pacing; matching groups with equal specificity use their strictest interval. Access restrictions are checked before requests and redirects with the existing robots permission parser.

A malformed matching pacing directive stops the scan for review rather than silently ignoring it. A required interval longer than 60 seconds also stops the scan before another request, avoiding a long sleep through the existing source-step timeout. It does not disregard the rule. Robots 429/403 failures stop access; the existing robots-404 behavior remains unchanged. This is conservative access handling, not proof of permission to crawl; the owner's rules still govern.

## Reading the diagnostics

Public summaries expose only fixed stage names (`robots`, `homepage`, `listing`, `sitemap`, `product_page`, or `cooldown`), request-attempt counts including redirects, the last available HTTP status, the consecutive-429 count, the chosen cooldown and the next eligibility in Mountain Time. Connection failures may have no HTTP status. A deferred scan explicitly reports zero attempts and does not claim to have checked whether the server still returns 429.

The encrypted detailed report retains a bounded request trace: exact URLs, stage, status, elapsed time and a small header allowlist (`Retry-After`, `Date`, standard/common rate-limit headers, `Server`, and `CF-Mitigated`). Cookies, authorization headers, entire header dumps and response bodies are excluded. Exact URLs may contain private product paths or query values: never copy the raw trace into public issues or logs. The existing Actions entrypoint seals these details in `detail.enc`; database/source reports remain in encrypted state. Local standalone reports are private plaintext files and must stay in protected storage. Do not upload those files directly.

In a failed scan, successful page reads and a partial product list are **not** promoted to a verified snapshot. The previous catalog, saved import and historical records remain intact. No product is discontinued from a failed/incomplete scan. A successful complete snapshot still yields disappearance candidates requiring the existing review process. Source outcomes are recorded in the existing `source_checks` audit history.

## Review and deployment considerations

This phase adds neither caching nor alternate feeds/endpoints, and it does not remove the existing full-catalog/product-page work. Slow scans can still hit the source-step timeout. Host-based blocking may persist regardless of pacing. No live access success is promised by mocked tests.

Review the draft PR and offline test results before separately approving merge/deployment. Keep the existing encrypted artifacts as recovery points. After an approved deployment, inspect the next eligible scheduled run's stage/count/status and encrypted headers before recommending another change. Do not manually trigger a live diagnostic run without explicit authorization. If the owner does not permit this access, pursue an owner-approved feed or manual export in a separately approved phase.

# Running and maintaining the reference system

For the new 8:00 AM Mountain GitHub Actions option, follow [Activate daily GitHub monitoring](GITHUB_ACTIONS.md). The systemd instructions below describe the earlier local-host alternative; do not activate both schedulers against the same baseline.

## Plain-English operating status

The code is prepared to check sources. That does not mean a computer is currently running it every day. Check these separately: can it reach each source, can it read private files if needed, does it retain its database, and is its daily schedule actually active?

`python -m gloformulas.cli schedule-status` inspects this host's system-level systemd timer without changing settings. It reports no confirmed schedule when the timer is absent or inactive. It does not claim that a hosted computer will remain available. On a computer using a different scheduler, confirm the task and recent successful runs separately.

## Source inputs and outputs

Private source configuration uses the original guide URL, supplier sheet URL and COA folder URL. Keep it outside version control. Saved imports are JSON, preserving both the raw source and structured fields. The public repository contains no real document IDs or source data.

Daily output is saved under the private state directory:

- `reports/latest.md`: readable status/change summary, with explicit failed and unavailable sources.
- `reports/latest.json` and a versioned run report: source results, fresh-versus-replayed status and change counts.
- `reference-guide.md`: a review draft, not a published replacement of the original Doc.
- `research-coverage.json`: each product's uses, interactions, precautions and evidence-gap review coverage.
- `reference.sqlite`: records, source versions, lab artifacts and audit history.

The standalone commands accept `--db PATH` before their command. For example:

```sh
python -m gloformulas.cli --db work/reference.sqlite supplier-compare work/imports/sheet.json
python -m gloformulas.cli --db work/reference.sqlite daily --state work
python -m gloformulas.cli --db work/reference.sqlite daily --live --state work
```

An offline run never claims to have checked the current websites. Exit code 2 means some sources were blocked/incomplete; inspect the saved report. Exit code 1 means an unhandled command failure. A completed source scan is separate from medical or batch verification.

## Daily schedule preparation

`ops/recurring.sh` runs independent live checks with an exclusive lock and retains a consistent SQLite backup even after partial source failures. It never publishes the Google Doc. Set `GLO_STATE_DIR` to private persistent storage and `GLO_SOURCE_CONFIG` to the private source settings file. `GLO_PYTHON` can select the installed virtual environment's Python executable. No Google connection is mandatory for public-source checks; private Google reads are opt-in.

`ops/gloformulas.service` and `ops/gloformulas.timer` are Linux systemd templates. Paths must match the selected computer. The proposed timer is daily at 09:00 UTC, with up to five minutes jitter: 2 a.m. in Boise during standard time and 3 a.m. during daylight time. This is a proposal, not an active schedule. Choose a different time before activation if preferred. No scheduler was installed or enabled during this work.

On the chosen Linux host, install the code/dependencies, configure private state/network access, run one live check, inspect its report, and only then install/enable the timer. Confirm `systemctl is-enabled gloformulas.timer`, `systemctl is-active gloformulas.timer`, and `systemctl list-timers gloformulas.timer`. Inspect the service journal and a successful saved report after the first actual scheduled execution. Other operating systems need their equivalent local scheduled task. An existing always-on computer can be used; paid hosting is optional.

Keep backups private. Define a retention period, periodically restore a backup into a separate directory, and run SQLite integrity checks before relying on recovery. Never run this public repository's untrusted pull requests on a runner that holds private Google credentials.

## Optional read-only Google sync

The chat's connector cannot be borrowed by a standalone job. Use either authorized-user OAuth or a service identity shared onto the specific files, using Google's supported APIs. Do not change source sharing merely to avoid authentication.

```sh
python -m pip install '.[google]'
python -m gloformulas.cli google-sync --directory work/imports
```

The sync asks for read-only Drive, Docs and Sheets scopes. It paginates the folder and reads all rows in columns A:Y of the known supplier tab. It does not change files. Google APIs must be enabled on the selected project. Credentials belong outside Git, preferably via a supported identity provider or securely stored authorized-user credentials selected by ADC. No credentials were requested or configured here.

The sheet's public CSV export is tried by `supplier-fetch` only if its owner already permits it. A sign-in page or denied export is reported as blocked. Existing chat-connected reads can still supply saved snapshots for manual runs.

## Separate publication operation

The original document remains unchanged. Publication is never part of the scheduler. A future, reviewed update requires an explicitly generated plan and a separate `publish-plan` operation, which uses a write-capable Google authorization. Plans revalidate the original document ID, current revision, current claim approvals, evidence context and review dates. Mixed-format claim paragraphs are blocked to protect layout. Native tables and unrelated content are left intact. Table edits/new sections and rendered-layout validation are still outstanding work before a complete regenerated native guide can be published.

## Current integration limits

The real GloFormulas website could not be opened by this session's web reader. The executor has enforced restricted network access and no Google runtime identity. The Freedom lookup instructions could be read, but actual company results and PDFs were not obtained from the live lab site. The rendered adapter's current selectors follow that observed interface and remain provisional until a successful live smoke check. The Europe PMC client has simulated pagination tests; no live API result has been retrieved here. These are disclosed integration limits, not reasons to purchase hosting or submit credentials before further review.
