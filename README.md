# GloFormulas Living Peptide Reference Database

This system preserves the original peptide guide, watches for source changes, and keeps a reviewable research record. It never turns a laboratory certificate into a claim that a product is sterile, safe, effective, or safe to combine with another peptide.

**The original Google Doc has not been changed. A GitHub Actions daily workflow is now prepared for 8:00 AM Mountain Time, but activation and a scheduled execution are not yet confirmed.**

For Codex Cloud without a terminal, use the [browser-only encrypted database transfer](docs/BROWSER_TRANSFER.md). GitHub prepares a public recipient, Codex uploads only ciphertext, and Actions creates the standard seed without revealing the repository secret.

Use [Activate daily GitHub monitoring](docs/GITHUB_ACTIONS.md) for the exact setup and manual Run Now steps. It requires no paid host: one encryption secret and a one-time encrypted transfer of the existing private database keep state secure in this public repository. The public repository contains code and synthetic tests; private source data, Google file IDs, reports, database files, and credentials stay out of Git.

## What is built

| Part | What it does | Current verification |
| --- | --- | --- |
| Product catalog checker | Reads public product feeds or sitemap product records; compares names, sizes, stock status and listing content | Tested with simulated websites. Actual GloFormulas website remains inaccessible from this session; its platform and readable fields still need a live compatibility check |
| Freedom Diagnostics checker | Searches the official lookup, accepts only GLO-prefixed report codes, downloads official-host PDFs, preserves original bytes and extracts printed fields | PDF extraction and all review rules tested. Actual search results and the optional rendered search adapter still need a live compatibility check |
| Supplier comparison | Compares products, batches, and report links; ignores row movement; keeps supplier evidence separate | Tested with simulated changes and a fresh read of the real connected spreadsheet; no changes found in that read |
| Evidence and interaction tracker | Stores source dates, study context, evidence quality, source locations, limitations, review dates and interactions | Tested. Original claims remain pending; the literature discovery adapter still needs live endpoint validation and actual medical source review |
| Daily checks | Runs sources independently, restores encrypted GitHub state, and creates dated summaries/private details | Tested locally with simulated live services and saved real snapshots; GitHub activation still required |
| Original guide protection | Daily checks never call the Google Docs publishing function | Original unchanged; current proposed update plan has zero edits |

A failed or incomplete catalog scan does not mean products were discontinued. A missing search result does not mean a COA does not exist. A matching supplier batch does not prove that a particular GloFormulas vial was tested. These uncertainties appear in the review record.

## What you will eventually need to connect

No paid hosting is required. The new GitHub Actions workflow can provide the daily computer: follow the activation guide rather than installing the older local systemd timer. An existing private computer remains an alternative. GitHub scheduling is not active/confirmed until the workflow is merged, initialized, enabled and observed.

1. **Website access:** that machine needs permission to reach GloFormulas, Freedom Diagnostics, and Europe PMC. This workspace currently restricts those network destinations. A successful live check must confirm each website's actual structure before treating an adapter as operational.
2. **Google access, only when needed:** the chat's Google connection can read sources during a conversation. A standalone daily program cannot reuse that chat connection. It can try the supplier sheet's existing public export without credentials; if that is private, authorize read-only access or supply a fresh exported snapshot. Do not make private files public just to avoid authorization. Reading private Google files automatically needs supported read-only Google authorization; editing the original document remains a separate, later action.
3. **Freedom report linkage:** GLO codes are candidates. Before tying a report to a specific product/batch, retain the vendor's batch evidence and a named linkage review. No laboratory login is assumed; any login or access barrier becomes a reported blocker.
4. **Medical review:** a qualified reviewer must confirm the actual source and its applicability before medical claims or interactions are marked approved. Discovery results are leads, not verified clinical conclusions.

You do not need to connect or authorize these before the offline work and tests are complete. No passwords, credential files, or payments should be sent in chat.

## Local developer quick start

Python 3.11+; the core uses Python's standard library. Optional extras add PDF extraction, a rendered browser lookup, or Google API access.

```sh
python -m unittest discover -s tests -v
python -m gloformulas.cli init
python -m gloformulas.cli daily --state work
python -m gloformulas.cli schedule-status
```

`daily` without `--live` checks saved snapshots only and says so. A partial check exits with status 2 after saving its report. Snapshots are supplied under `work/imports/`: `guide.json`, `sheet.json`, `folder.json`, and optionally `catalog.json`. Do not use synthetic examples as production baselines.

For live-capable adapters after network access is available:

```sh
python -m pip install '.[lab,browser]'
python -m playwright install chromium
python -m gloformulas.cli catalog-live --adapter auto
python -m gloformulas.cli freedom-live --rendered --code GLO_EXAMPLE_ONLY
python -m gloformulas.cli supplier-fetch
python -m gloformulas.cli daily --live --state work
```

The example code is synthetic. The browser dependency needs a Chromium installation on the chosen host; no browser was installed or live lookup completed here. A bot challenge, missing readable data, changed search form, or blocked request is reported instead of bypassed. The HTTP client retains configured proxy/TLS settings, limits hosts/downloads/retries, and obeys catalog robots rules.

## Private configuration

Set `GLO_SOURCE_CONFIG` to a private JSON file; this session's ignored configuration is `work/source-config.json`. It contains `GUIDE_URL`, `SHEET_URL`, and `FOLDER_URL`. Optional settings:

- `CATALOG_ADAPTER`: `auto`, `woocommerce`, `shopify`, or `sitemap`. Auto-detection uses the actual homepage; it does not assume a platform.
- `FREEDOM_RENDERED_LOOKUP`: enable the visible public lookup browser adapter after its live smoke check. Otherwise an unreadable static lookup is a blocker, not an empty result.
- `FREEDOM_CODES`: known printed GLO-prefixed search codes. With no codes, company discovery searches GloFormulas. Unrelated returned reports are rejected after reading their printed code.
- `FREEDOM_EXPECTATIONS`: reviewed product/batch mappings, keyed by canonical search code; see the verification documentation.
- `RESEARCH_PRODUCT_IDS`: product database IDs selected for bounded literature discovery. Unresolved codenames are not searched as established identities.
- `GOOGLE_READ_SYNC`: defaults off. When explicitly enabled, tries the optional read-only Google API sync. Failure does not stop the other sources.
- `SHEET_GID`: supplier tab identifier, default `0`.

## Data, evidence and publication

The first import preserved 46 guide headings, 45 table size labels, 52 lot records, 58 guide COA rows and 306 supplier-report links. Twelve identities remain unresolved. Full raw guide blocks and source snapshots are retained, including formatting and irregular blend tables. Narrative-only sizes still need review. Supplier report links are not automatically parsed or authenticated by Janoshik; this phase adds supplier spreadsheet comparison, not an independent Janoshik verification service.

The database keeps immutable source versions, product/formulation/size/batch records, lab results, original PDF bytes, research candidates, claim context, interactions, flags, changes, run outcomes and an audit history. Repeated imports are idempotent; a source reverting to an earlier version is still recognized as a change. Source-specific schemas are checked before changing comparison baselines. The same exact source data never becomes proof of a medical claim just because it is re-imported.

Medical categories are researched uses, concurrent-use evidence, precautions and insufficient evidence. Every product has a coverage record for all four. Claims require primary-source review and context before approval. Expired reviews and observed retractions are withheld from generated prose. Interactions cannot be labeled “safe”; a reviewed interaction record applies only to the documented population, route, and evidence. Research discovery is bounded and does not establish that all literature or interactions have been found.

The optional Google API publisher remains separate from daily checks. It uses the original revision number to avoid overwriting concurrent edits and touches only approved claim paragraphs with simple formatting. Automatic table updates and new product sections are still a later formatting-aware extension. No unverified medical information is published.

See [Operations](docs/OPERATIONS.md) for commands, schedule preparation and recovery; [Verification and research](docs/VERIFICATION.md) for source rules, review steps and known limitations.
