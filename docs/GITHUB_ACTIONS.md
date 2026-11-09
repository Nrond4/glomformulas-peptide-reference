# Optional Google Docs publisher (version 0.4)

The source monitor remains read-only. A separate, default-disabled publishing stage can now maintain a factual updates section after a test-copy gate. Follow [the browser-only connection, activation and recovery guide](GOOGLE_DOCS_PUBLISHING.md). Earlier statements below about no Google credentials or document writes describe the monitor-only configuration; enabling the optional publisher adds short-lived Google federation after source checking. Existing Google source synchronization remains disabled.

# Activate daily GitHub monitoring

This is the new unattended option. You do not need paid hosting or another service. The workflow uses a standard GitHub-hosted Linux runner and GitHub artifacts. It does not ask for a personal access token or Google credentials, and it cannot publish the original Google Doc.

The implementation checked `main` first: only `.github/workflows/test.yml` existed. The new `.github/workflows/daily-monitor.yml` runs at **8:00 AM Mountain Time** using `America/Boise`, automatically following daylight saving time. It also has a **Run workflow** button for a manual check. GitHub can queue a scheduled job after 8:00; it is not an exact-time guarantee.

## What you must do once

### 1. Merge the monitoring pull request

Merge this change into `main`. Scheduled/manual workflows must exist on the repository's default branch. Open **Actions** in the repository and allow Actions if GitHub displays an enable button. The new workflow is named **GloFormulas Daily Monitor**. The normal test workflow continues separately.

Scheduled monitoring remains gated off until you set `GLO_MONITOR_ENABLED=true` in step 5. You can test manually first.

### 2. Create one encryption secret

The repository is public. GitHub artifacts can be downloaded by other readers, so the database and detailed reports are encrypted before upload. Public summaries show only counts and source statuses, without private source names, file IDs, URLs, report text or raw errors.

Use a password manager to generate a new random secret of **at least 32 characters**. Keep a backup of it in the password manager. In the repository, choose:

**Settings → Secrets and variables → Actions → Secrets → New repository secret**

- Name: `GLO_STATE_KEY`
- Secret: the random value you generated

Do not put the key in a repository file, workflow input, issue, chat, screenshot or release description. The same key is needed to create the initial encrypted database and decrypt downloaded detailed reports. Losing or replacing it prevents access to the existing encrypted history. This is an encryption key, not a Google password or a personal access token.

### 3. Transfer the existing database once, securely

**Using Codex Cloud in a browser? Use the [browser-only transfer steps](BROWSER_TRANSFER.md).** No terminal, key sharing or raw database download is needed. After merging the transfer workflow, run **Secure Browser Database Transfer → Prepare transfer**, send Codex the successful run link, then run **Finish transfer** after Codex confirms the encrypted upload. Finish verifies all 46 original records and automatically attaches `monitor-seed.enc` to the `monitor-seed` release.

The original database remains in the private workspace until Codex encrypts it for the public recipient created by Prepare. The workspace never needs access to your `GLO_STATE_KEY`. Only encrypted research bytes leave the workspace; the repository secret and encrypted private recipient key stay on the GitHub side. Do not create an empty substitute or upload the raw `reference.sqlite` file.

If a normal private terminal is available, the existing `actions_monitor seed` helper remains an alternative. It is not required for the browser-only method.

### 4. Test with “Run Now”

Open **Actions → GloFormulas Daily Monitor → Run workflow**:

1. Select branch **main**.
2. Check **First run only: initialize from the encrypted monitor-seed release**.
3. Click **Run workflow**.
4. Open the run when it finishes. Read the Summary and download its dated `glo-monitor-report-…` artifact.

The workflow restores and checks the original database before contacting sources. A missing key, wrong key, expired/corrupt state or missing seed is a visible failure. It never substitutes an empty database. Once a state artifact exists, it takes precedence even if the first-run box is accidentally checked again.

Run it a second time with the first-run box **unchecked**. This confirms that the new run restores the preceding run's database instead of resetting the baseline. Both runs may be red/partial if some live sources are unavailable. A partial run still saves its updated encrypted database and a report explaining which sources were not checked.

**Successful scheduling is separate from successful source connections.** A green setup step does not mean all websites have been verified. Inspect source-by-source status and the first actual scheduled execution.

### 5. Turn on the daily schedule

After testing, choose:

**Settings → Secrets and variables → Actions → Variables → New repository variable**

- Name: `GLO_MONITOR_ENABLED`
- Value: `true`

The workflow will be eligible at the next 8:00 AM Mountain schedule. Open Actions after that time and confirm an actual run with event **schedule**, not just a manual run. Setting the variable to `false` pauses daily checks while keeping the manual button available.

No additional token, Google credential, paid account or third-party hosting setup is required for public website checks. Keep GitHub Actions enabled for the repository. GitHub may delay jobs or disable scheduled workflows on inactive public repositories; periodically check recent executions and repository notices.

## What each run checks and stores

- Reads available GloFormulas catalog data and product pages for names, vial size labels, stock/listing changes and possible COA links.
- Looks up GloFormulas/GLO reports at the official Freedom Diagnostics interface. Only GLO-prefixed printed codes can be accepted; a discovered link or PDF is not automatically a verified product/batch match.
- Compares the supplier sheet if its existing public export permits reading. Private Google files are not silently made public or accessed with write permission.
- Keeps original guide/folder snapshots for comparison. These are explicitly marked as saved copies, not freshly fetched Google data.
- Runs the already-configured literature discovery queue, if any; medical approval stays manual.
- Reports unavailable sources, errors, changes, and review flags. A source failure cannot become a “no products” or “no COAs” conclusion.

The workflow never invokes the Doc publisher. It forces Google API sync off and receives no Google credentials. No unverified COA or medical claim is approved by a scheduled run.

The visible run summary and `summary.md` contain safe counts/statuses and fixed diagnostic categories only. The same sanitized summary appears in the **Show safe summary** job-log step, so Codex can inspect a run link without requesting private downloads or keys. Raw errors, URLs and report text remain encrypted. Detailed findings, before/after records and source errors are in `detail.enc`. The database carried forward to the next run is in a separate `glo-monitor-state` artifact containing only `state.enc`. Raw logs, source files, PDFs and databases are never uploaded in plaintext or committed.

Detailed reports are retained for 14 days and state artifacts for up to 90 days, subject to the repository's retention limits. Runs do not delete previous artifacts or commit generated files. Download an encrypted state backup periodically. If state expires after a long interruption, the workflow stops for recovery rather than silently resetting history. Current local baseline size is small; larger histories can consume GitHub's included artifact storage. A configured size limit stops oversized exports instead of introducing another storage service or automatic paid upgrade.

## Reading a detailed report

Download and unzip the dated report artifact. Read `summary.md` directly. For the private findings:

```sh
python -m pip install '.[monitor]'
python -m gloformulas.actions_monitor decrypt-report detail.enc --output work/decrypted-report
```

Enter the encryption key when prompted. `work/decrypted-report/report.json` contains the dated full findings and errors; `changes.md` contains the readable change report when the run completed far enough to generate one. Treat decrypted files as private. The database artifact uses a different authenticated envelope and cannot be mistaken for a report.

## Live connections that still need confirmation

The previous work could not reach the actual GloFormulas catalog from the restricted workspace. The live site platform/fields still need a successful compatibility check. The workflow can now try from a GitHub-hosted runner; unsupported/custom-rendered pages remain reported failures until adapted to the observed site.

The Freedom interface was observed, but its actual result pages/current PDF layouts have not yet passed a live end-to-end check. Chromium is installed for the public lookup. A changed form, bot challenge, unreadable/scanned report or external download host is reported as unavailable/pending rather than bypassed or invented. Company discovery cannot currently prove exhaustive report coverage; known printed GLO codes can be configured for individual verification.

The supplier sheet's public export may be blocked. Your chat's Google connector is not reusable by an unattended runner. Existing snapshots are retained, and no Google authorization is required to test the workflow. If automatic private-sheet/Drive refresh is wanted later, add a separate supported **read-only** connection; this workflow intentionally does not configure one now. Literature discovery also still needs a successful live endpoint check if enabled. Product/batch linkage and medical source review remain human review tasks.

The optional `GLO_SOURCE_CONFIG_JSON` repository **secret** can override approved non-credential source settings, such as known GLO codes or selected research product IDs. Never put these private settings in ordinary Variables or workflow inputs. `GLO_SEED_TAG` is an optional non-secret variable if you use a different release tag. Google read sync remains disabled even if an override requests it.

## Implementation references

- [GitHub schedule syntax, timezone and daylight-saving support](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#onschedule)
- [Artifact access and retention](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/download-workflow-artifacts)
- [GitHub artifact API](https://docs.github.com/en/rest/actions/artifacts)

If a repeat run fails before source checks, inspect its fixed `Setup diagnostic` category in the restore-step log. The artifact ZIP API uses GitHub JSON negotiation; release-asset downloads use octet-stream. Neither path forwards the job token to signed storage URLs. Do not reinitialize or change keys merely because a download failed.

## Source connection diagnostics after a successful restore

A successful encrypted restore means database transfer/persistence is working; it does not mean every source permits automated reads. The first successful repeat restore reported a rate-limited catalog, a blocked supplier download, a Freedom lookup timeout, and no literature selection. Saved guide/folder copies were explicitly marked as replayed.

The supplier reader now permits Google's `googleusercontent.com` file-serving subdomains for CSV redirects only, without broadening the catalog/lab clients or sending credentials. A sign-in redirect stops with a fixed authorization-needed message. This fixes a rejected-redirect case but does not make a private spreadsheet public or configure Google authorization.

Freedom recognizes official **Open PDF** links even when their URLs have no `.pdf` suffix. Reports must still download from official lab hosts, contain PDF bytes, have a printed GLO-prefixed code, and pass the existing checks. Page-load, search-control, and result-wait timeouts have separate safe diagnostic categories. A timeout/no readable result is never proof that no COA exists.

Catalog HTTP 429 responses retain the declared retry delay, including HTTP-date headers. The encrypted database carries the resulting cooldown to the next manual/scheduled job. A job during that cooldown marks the source **deferred**, preserves the prior baseline, and continues other checks. When no retry delay is supplied, the fallback cooldown is one hour. It does not bypass a block or turn an unavailable scan into a green/no-change result. A later permitted request must still establish catalog completeness.

These fixes are tested locally; the next GitHub-hosted run is still required to validate live compatibility. Known GLO codes and research selections can be added later through private settings; no verified codes were found in the preserved guide to invent a default. Original Google Doc publishing remains disabled.

## Known Freedom report codes: direct PDF first

For configured `FREEDOM_CODES`, the monitor now tries the observed official report route on `coas.freedomdiagnosticstesting.com` before using the interactive lookup. The downloaded file must contain PDF bytes, come from an approved official host, and have the same **printed** GLO search code as requested. It is not accepted simply because its filename begins with GLO. Only HTTP 404 permits fallback to the public lookup; rate limits and access denials do not trigger another route.

This uses the existing `GLO_SOURCE_CONFIG_JSON` / `FREEDOM_CODES` setting. No additional secret, report URL setting, database initialization, or encryption-key change is required. Company-name discovery remains the path when no specific codes are configured. Known-code monitoring does not establish complete discovery of all new reports, and the public summary now states that coverage limit.

If text extraction or the printed code cannot be verified, the original official-host PDF is retained only in the encrypted database and a review flag is added. A layout-preserving text fallback may fill unreadable fields but cannot select one of conflicting printed search codes. Missing data is not filled from a filename. Printed code mismatches/non-GLO reports are rejected as GloFormulas records. Laboratory-host retrieval remains separate from reviewed product/batch linkage and medical evidence.

The direct path and extraction safeguards have synthetic PDF tests. The user-supplied live report could not be retrieved by this workspace's reader; its printed identity, date, batch, purity and content have not been verified here. Inspect the next GitHub-hosted run after merging rather than assuming that a successful unit test authenticates the real report.
