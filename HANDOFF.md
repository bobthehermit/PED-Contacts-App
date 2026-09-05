# HANDOFF — SBB Cash Report Extractor

Written for whoever picks this up next, including future-me. `README_cash.md`
documents *how* the tool works. This documents *why it works the way it does*,
and the reasoning that is expensive to rediscover.

Parser version at handoff: **2026.09.02.5**

---

## What this is

New Mexico's 190+ LEAs file a quarterly cash report — an Excel workbook — into a
per-entity SharePoint subsite. Six fiscal years of those workbooks existed only
as ~5,900 individual files nobody could query.

This pipeline crawls them, parses them, and writes partitioned parquet:

- **1.66M rows** across `cash_lines`, `cash_bank`, `cash_explanations`
- **4,283 of 4,326** filings parse OK
- Coverage runs 180–190 of 193 entities per quarter, FY21–FY26

**Validation anchor:** Belen FY26 Q3 total ending cash = **$43,931,135.67**
across **35 funds**, bank-to-book variance $0.00. That number is straight from
the workbook. If a change breaks it, the change is wrong. Check it after any
edit to parsing.

---

## Five principles, learned the hard way

### 1. The folder is the system of record

**Folder wins over the workbook for both fiscal year and quarter.** Always.

This looks wrong. The workbook seems more authoritative — it has a title, a
report end date, dated line labels. Trusting it is a mistake made twice during
development, and both times it corrupted data.

The reason: the template **derives** the report title, the Line 1 date, the
Line 4 date, and the report end date from a single "Previous Year End" cell.
Those four fields are not four signals. They are one cell's typo, repeated.

Jemez Valley's FY24 filings have that cell set to 3/31/2024 (it should be
6/30/2023), so the title reads "2024-2025", the report end date reads 6/30/2025,
and every derived date is a year out. Cross-referencing them just gets four
copies of the same mistake agreeing with each other.

A business manager types the workbook. PED files the folder. When they disagree,
trust the filing system. The workbook's claim is preserved in
`fiscal_year_workbook` / `period_label` and the disagreement is flagged.

> The user's own framing, which is the clearest statement of the rule:
> *"if i pulled this report from the q1 folder and looked at the title, i'd
> ignore the q2 and say 'they messed up the heading, this should be q1'"*

### 2. Identity is the PED number, resolved in three tiers

Entity **names are not stable**. 21st Century Public Academy (580-001) appears
across six years as "21st Century Public Academy", "State Charter", "Twenty
First Century (21st CENTURY PUBLIC ACADEMY)" and "21ST CENTURY PUBLIC ACADEMY".
On the FY21/FY22 template, `School District:` is the **authorizing district** for
a charter, so ABQ Charter Academy reports "Albuquerque Public Schools".

`EntityKey` is therefore the PED number. But the PED cell itself is unreliable,
so `_resolve_ped_numbers()` repairs it in tiers:

1. **By entity name** — consensus across the corpus
2. **By SharePoint folder** — survives a blank name (Bloomfield FY24 has an
   empty School Name cell *and* a `#N/A` PED number)
3. **By cross-folder conflict** — one PED number belongs to exactly one LEA.
   South Valley Academy typed `025-000` (Santa Rosa's number) into all four FY21
   workbooks. The folder that uses a number more often owns it; filings in the
   other folder are reassigned to their own folder's dominant number.

**The unanimity rule in tiers 1 and 2 is load-bearing.** Dream Diné legitimately
has two PED numbers — 067-109 while locally chartered, 559-001 after converting
to a state charter. Both appear only in the `DREAM DINE` folder, so there is no
consensus and no cross-folder conflict, and it is correctly left alone. Relaxing
unanimity would silently collapse it onto one number.

Charters do convert and get renumbered — this has happened in the Taos area,
Cimarron, and APS. Neither name nor number is permanently stable, which is why
every fact row also carries `entity_folder` and `source_path`.

### 3. pandas 3.0 is required, not preferred

`select_candidates()` builds its grouping key with
`.astype("Int64").astype(str).fillna("-")`. Under pandas 3, NA survives
`astype(str)` and `fillna` catches it. Under 2.x it becomes the literal string
`"<NA>"`, `fillna` does nothing, `groupby` drops the rows, and **every quarterly
filing silently fails to be selected** — no error, just a near-empty dataset.

Related, and worth internalising: **`bool(float('nan'))` is `True`**. Three
separate bugs came from truthiness checks on pandas values —
`int(float('nan'))` in a flag, `groupby(dropna=False)` lumping every unkeyed
filing into one fake group, and `if x` letting NaN through a dict filter. All
three now have explicit `pd.notna` guards. Be suspicious of any bare truthiness
check on a value that came out of pandas.

### 4. Flags are for training business managers, not for filtering

This reframes what to build. Flags look like noise to suppress; they are
actually a list of specific, attributable, checkable errors with a person's name
attached.

> *"if there's wrong data, i use that to guide and train BMs"*

`04_findings.py` exists for this. It reports per-section counts plus a "most
frequent" list of which entities repeat each error. The highest-yield items so
far:

- **58 filings with no bank accounts listed** — the form cannot reconcile by
  construction. Binary and unambiguous.
- **Prior year end not June 30** — one cell that shifts four derived fields.
- **Another LEA's PED number** — 12 filings.

Genuine findings already surfaced: Alamogordo with negative operational cash all
four quarters of FY26 (they failed to budget payroll liabilities), Jefferson
Montessori with negative operational *and* a −$200 variance every quarter, APS
off by $18–22K on its own reconciliation every quarter.

Domain nuance from the user: **only 24xxx is uniform** (federal flowthrough,
reimbursement basis, should be negative). 27xxx is state flowthrough on the same
basis. 25xxx/26xxx/28xxx/29xxx are genuinely mixed — some advance-funded, some
reimbursed directly by the federal government or an REC — so `FUND_EXPECTATION`
marks them `either` rather than asserting a sign and generating noise. 22000
athletics and 13000 transportation should never go negative; 21000 food services
legitimately does, because USDA reimbursements are large and late.

### 5. Structure over naming, everywhere

The form's *shape* is stable across six template versions; its *coordinates* are
not. Nothing is parsed by hardcoded cell address. The entity label alone has
been "School District:", "School Name:" and "Entity Name:", in cells A4, A2 and
B2.

Same principle for the sheet itself: `find_cash_sheet` matches the name
`CASH REPORT` first, then falls back to finding a sheet that structurally *is* a
cash report (≥4 known fund codes followed by "Line 1" in column A). A workbook
re-saved with the tab renamed `Sheet1` still parses.

The rule that does the heavy lifting: **a fund header row carries ≥4 known fund
codes AND is followed by "Line 1" in column A.** The second clause is what
distinguishes real fund blocks from the FUND/AMOUNT explanation grids at the
bottom of the sheet, which also carry fund codes. If a future template change
breaks parsing, check that assumption first.

---

## Environment

| | |
|---|---|
| **Extract** | Windows workstation only — needs the `X:` mount |
| **`X:`** | WebDAV to `https://webed.ped.state.nm.us/sites/FileTransfer`, **not** OneDrive sync |
| **Versioning** | MacBook Air M2 — **GitHub is blocked at device level on the work machine** |
| **Transport** | external drive, manual copy. Repo is private. |
| **Everything but `localize.py`** | pure Python, runs anywhere |

`X:` is slow. Enumerating 15,505 files takes **~57 minutes**. Do not parse
against it directly — `localize.py` copies the ~5,900 cash workbooks (~0.5 GB)
to local disk in ~6 minutes with a thread pool, and re-parses are then free.

**Never `robocopy X:\ ... /S`.** `Financial Reporting` is one of ~12 sibling
folders per entity per fiscal year, so a root-level recursive copy enumerates
100,000+ files to find 5,900. `localize.py` reads paths from the inventory CSV
and copies them directly.

PowerShell execution policy is `AllSigned` — unsigned `.ps1` files will not run
from disk, only when pasted into the console. That is why the pipeline is Python.
`00_setup.ps1` and `01_inventory.ps1` are kept for reference;
`localize.py --refresh-inventory` supersedes the latter.

---

## Files

| File | Purpose | Runs on |
|---|---|---|
| `localize.py` | inventory `X:`, copy cash reports to `raw\` | Windows |
| `cash_extract.py` | crawl `raw\`, parse, write parquet | anywhere |
| `03_review.py` | *is the pipeline working* | anywhere |
| `04_findings.py` | *what did it find* — the BM training lists | anywhere |
| `cash_explorer.py` | Streamlit explorer | anywhere |
| `config_example.py` | copy to `config_local.py` (gitignored) | — |

`PARSER_VERSION` prints on every run and is stamped into the manifest.
`--incremental` invalidates its cache when it changes — mtime tracks the file,
not the code reading it. **Bump it whenever parsing or flagging changes.**

---

## Open items

1. **`refresh_inventory()` ignores `--first-fy`** — it walks every FY folder
   regardless. Fixing this is what makes a nightly current-year-only run cheap
   (~15 min vs ~57).
2. **No inventory diff.** Keeping the previous CSV and reporting what appeared,
   vanished, or changed size would directly answer "did someone delete an FY21
   file", which is a live concern — nothing about this data is locked.
3. **Hardcoded paths** in `localize.py` and `01_inventory.ps1` should move to
   `config_local.py`.
4. **Publish convention undecided.** Proposal: write three *consolidated*
   parquet files (`cash_lines.parquet` etc.) plus the two manifests to a
   Drive-synced folder, rather than the 21-file partitioned tree — 21 Drive file
   IDs becomes 24 when FY28 arrives. Apply the same convention to `obms_data`.
5. **`FUND_EXPECTATION` is still partly a first guess** outside 24xxx/27xxx. The
   Fund signs screen in `cash_explorer.py` exists to replace guesses with
   evidence.
6. **24 FAILs cluster** — Pecos Cyber Academy 9, Native American Community
   Academy 7. Two template variants, probably one fix each.

## Roadmap

1. Fix `refresh_inventory --first-fy`; add the inventory diff
2. Move paths to `config_local.py`
3. Decide and implement the consolidated publish step
4. Task Scheduler: **nightly** current-FY (~15 min), **weekly** full (~70 min)
5. Let it run a week unattended before building on it
6. Then: actuals review app integration, Power BI tab

Steps 1–3 are one focused session. Step 5 is the one people skip and regret.

## Not yet done, deliberately

**No OBMS join.** Lines 2 and 5 are self-reported as *"Per OBMS Actuals
Revenue/Expenditure Report"*, so joining on (PED no., FY, fund) gives an
automated tie-out of what districts *said* they pulled against what OBMS
actually holds. The decision was to visualize the cash data on its own first and
confirm it makes sense before wiring it to anything.

When that happens, the fragile part is `normalize_entity()` / the key columns
matching `obms_extract.py`'s output. Diff them before trusting any tie-out —
a mismatch drops rows silently, with no error.