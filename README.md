# GloFormulas living peptide reference

A provenance-first Python/SQLite research system. Phase 1 implements the database, lossless source snapshots, conservative imports, complete-catalog comparison, and a deterministic COA review engine. It also includes a draft guide generator, guarded Google Docs integration, audit records, tests, and deployment templates. It is not yet an unattended live research or laboratory verification service.

## Run locally

Python 3.11+; the core has no third-party dependencies.

```sh
python -m unittest discover -s tests -v
python -m gloformulas.cli init
python -m gloformulas.cli import guide work/imports/guide.json
python -m gloformulas.cli import sheet work/imports/sheet.json
python -m gloformulas.cli import folder work/imports/folder.json
python -m gloformulas.cli check
python -m gloformulas.cli guide work/reference-guide.md
python -m gloformulas.cli status
```

The local first import preserves 46 guide headings, 45 distinct table size labels, 52 lot records, 364 guide result rows/supplier report links, and 184 inherited medical statements. The local source snapshots and database are ignored by git. This repository is public: do not commit private source material, reports, credentials, snapshots, or database files.

## Data and version history

`gloformulas/schema.sql` defines products, formulation components, vial sizes, batches, lab results, evidence, immutable source versions, native guide blocks, flags, catalog snapshots/changes, run outcomes, and an audit log. SHA-256 source IDs make re-imports idempotent. Original rows and full guide content/styles are retained as JSON; irregular blend tables are not flattened into fabricated purity or batch fields. A component column can represent measured content, not necessarily a declared formulation. Formulation entries remain pending review. Sizes embedded only in narrative remain in raw blocks pending mapping. Product records retain their initial imported identity while newer source versions remain available for reconciliation.

The existing guide is a transcription source, not primary medical evidence. Its medical statements enter `pending/unassessed` and are excluded from regenerated medical prose. Their original text is preserved in the database. The supplier sheet explicitly identifies Cocer testing; no supplier report is attributed to a GloFormulas vial without independent batch linkage. Folder metadata is not a verified COA and the connector listing may stop at 100 files; API sync paginates.

## COA review

```sh
python -m gloformulas.cli verify examples/coa.json --url https://example.org/primary-report
```

Example files are synthetic, not real product reports. Intake stores identity, purity (including qualifiers such as `>99.9%`), measured content, lab, report date, lot, search code, source, attribution, review status, and raw report fields. It flags missing fields, date errors, identity/batch mismatches, duplicate lab/search codes, unusual quantities, unit conflicts, qualified purity, and missing blend-specific reports. Default review triggers are purity below 98% and content deviation above 10%; these are review heuristics, not product specifications or safety cutoffs.

A Freedom Diagnostics record is eligible for GloFormulas attribution only with a case-sensitive `GLO` search-code prefix plus a nonempty suffix, laboratory authentication and vendor/batch linkage attestations. The code alone proves nothing. Setting either attestation requires a named reviewer and verification artifact. Supplier testing stays supplier testing even if the lab report is authentic. All guide transcriptions remain unverified until primary report review. A result may be authentic and still contain mismatches or unusual values.

**Never infer sterility, clinical safety, efficacy, or safe concurrent use from COA results.** No automated combination recommendation is generated. Missing evidence is not evidence of safety.

## Medical evidence and guide updates

Each claim records product, category (`uses`, `concurrent_use`, `precautions`, `insufficient_evidence`), source, publication date, quality, reviewer, and review state. Add a JSON record using `add-evidence` with `product_id`, `category`, `claim`, `url`, and optional `publication_date`; then review the actual source before using:

```sh
python -m gloformulas.cli add-evidence work/claim.json
python -m gloformulas.cli approve EVIDENCE_ID --reviewer 'Reviewer name' --quality human_trial --publication-date 2020-01-01
```

Approval is an explicit human research action, never a model inference. Original guide claims cannot be approved directly; add the primary source and its supported claim. Publication dates must be ISO dates. Evidence quality categories do not establish applicability to every product, route, population, or combination. Review these in the claim and source artifact.

The Markdown draft follows the existing sequence of product/codename, uses, concurrent-use evidence, precautions, insufficient evidence, and COA data. Unreviewed prose is withheld. Google Docs plans touch only existing approved claim paragraphs, use UTF-16 indexes, restore the original text style, preserve paragraph styles and tables, and fail closed on mixed-style paragraphs. The initial plan contains zero edits because no new primary medical claims have been reviewed. Table updates and adding new product sections require a subsequent formatting-aware implementation; they are not silently rebuilt.

## Google authentication and API integration

```sh
python -m pip install '.[google]'
python -m gloformulas.cli google-sync --directory work/imports
python -m gloformulas.cli docs-plan work/imports/guide.json work/docs-plan.json
python -m gloformulas.cli publish-plan work/docs-plan.json
```

Create a private source configuration JSON with `GUIDE_URL`, `SHEET_URL`, and `FOLDER_URL` (the three source URLs), and set `GLO_SOURCE_CONFIG` to its absolute path. This session has a local ignored configuration in `work/source-config.json`. Source document IDs are not committed to the public repository. Enable Google Drive, Docs, and Sheets APIs in your Google Cloud project. Use supported Application Default Credentials: a workload identity/service account shared onto the specific source files, or authorized-user OAuth credentials with the needed scopes. `services()` lists scopes; Docs writes use the documents scope while Drive and Sheets are read-only. The chat connector's authorization is not available as runtime credentials. Prefer workload identity; store any authorized-user credential file outside the checkout and point ADC at it securely. Never paste secrets into chat or commit them.

`publish-plan` re-reads the original document, checks its revision and ID, regenerates the plan from current approvals, submits a requiredRevisionId guarded batch, and reads back. Concurrent edits fail instead of overwriting work. Reads have bounded retries; ambiguous writes are not retried automatically. Inspect and reconcile after uncertain writes. The original guide has not been edited by this implementation.

## Recurring checks and reports

`ops/recurring.sh` takes an exclusive lock, syncs Google sources, imports changes transactionally, writes a concise count/delta report, generates a private draft, and creates a consistent SQLite backup. Runs and failures are logged. Set `GLO_STATE_DIR` to a private persistent directory and supply ADC. An optional `GLO_CATALOG_FILE` is a normalized complete catalog produced by a trusted site-specific crawler; the example documents its contract. The engine rejects empty, partial, malformed and duplicate-key catalogs; absent products become **discontinuation candidates**, never automatic deletions. Stock status/listing hashes can detect listing changes independently of disappearance. A first snapshot establishes a baseline; it cannot show historical changes.

The systemd service/timer are deployment templates, not installed or enabled. Adjust paths and activate them on your private host after credential/network setup. The timer runs daily at 09:00 UTC with up to five minutes jitter. Keep state and reports on private storage, define retention, test backup restoration, and monitor service failures. CI runs synthetic tests only and never handles private Google sources. Do not put a self-hosted runner with private credentials on this public repository.

## Access and remaining implementation

This session could read the guide, supplier sheet and folder via the connected Google Drive tools. The catalog website was inaccessible through the web tool; the execution environment's enforced host policy excludes GloFormulas, Google APIs and lab sites, and has no runtime credentials. No live catalog baseline, laboratory authentication, source OCR, primary medical literature review, deployment, or recurring job activation has been completed.

Next steps:

1. Configure outbound access to the exact catalog, Google API/auth and laboratory hosts needed, without bypassing the environment proxy. Configure ADC and access to the source files.
2. Inspect the actual site platform and implement/test its paginated catalog crawler, stock semantics, content hashing and completeness checks. Persist crawl timestamps; reject stale feeds before comparisons. Until then local normalized snapshots support the comparison engine but not autonomous catalog retrieval.
3. Download original COA PDFs/images privately, retain byte hashes, and add OCR/extraction with field-level page provenance and reviewer confirmation. Add permitted official laboratory verification adapters. Current intake accepts normalized review records and original guide transcriptions; it does not authenticate web reports automatically.
4. Resolve vendor/batch linkage, unresolved product identities, narrative sizes, blend labels, and component-versus-whole-vial reports. Do not substitute supplier evidence for GloFormulas-specific testing.
5. Review primary medical sources for every peptide; record interactions, precautions and evidence gaps without inventing missing data. Implement scheduled literature discovery into a pending queue, not automatic publication.
6. Extend formatting-aware Docs updates to verified table cells and new sections; validate rendered layout on a template copy before production use.
7. Install the private scheduler, define retention and alert delivery, and verify recovery on the target host.
