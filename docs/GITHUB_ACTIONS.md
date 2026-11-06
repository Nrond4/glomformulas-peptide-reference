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

The existing database is deliberately not committed to Git. GitHub cannot compare against it until it receives a protected copy. A normal clone contains the program but does not contain the private `work/` directory.

Use the original private workspace, which contains `work/reference.sqlite`, `work/source-config.json` and the saved imports. From the project directory, run:

```sh
python -m pip install '.[monitor]'
python -m gloformulas.actions_monitor seed --state work --output work/monitor-seed.enc
```

The helper asks for the same encryption key without displaying it. It takes a consistent backup of the real database and includes only the approved source snapshots/settings. It rejects an empty baseline and excludes credential files. The result is `work/monitor-seed.enc`. **Only this encrypted file may be uploaded.** Never upload `reference.sqlite`, raw imports, source settings, `.env`, or the key to the public repository.

In GitHub, open **Releases → Draft a new release**. Create a tag named `monitor-seed`, attach `monitor-seed.enc`, and publish the release. The ciphertext may be public; the encryption key stays in repository Secrets and your password manager. This release is a one-time transfer/backup, not another paid service. No seed file or encryption key has been generated/uploaded on your behalf; the existing private database remains in the original workspace until this step is completed.

If your private workspace is no longer available, recover its private backup first. Do not click through an empty database as a substitute. A supported re-import of the original sources is another recovery option, with explicit notice that the old comparison history has not been recovered.

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

The visible run summary and `summary.md` contain safe counts/statuses only. Detailed findings, before/after records and source errors are in `detail.enc`. The database carried forward to the next run is in a separate `glo-monitor-state` artifact containing only `state.enc`. Raw logs, source files, PDFs and databases are never uploaded in plaintext or committed.

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
