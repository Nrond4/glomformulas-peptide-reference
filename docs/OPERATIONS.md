# Running and maintaining the reference system

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
