# PED Contacts Manager

**New Mexico PED — School Budget Bureau**

Merges the SBB analyst assignment roster with district and charter contact data,
resolves the naming inconsistencies between those sources, and maps every LEA to
its assigned budget analyst.

---

## What it does

The bureau's contact data lives in sheets owned by different teams, none of which
agree on how to spell a school's name, and only one of which carries a PED number.
This app reconciles them:

1. **Loads** five Google Sheets (assignments, districts, two charter tabs, overrides)
2. **Normalises** school names — strips editorial notes, punctuation, and boilerplate
3. **Matches** each charter source to the assignment roster by name, refusing to guess
   when a name is ambiguous
4. **Flags** unmatched and partially-matched schools for review
5. **Provides** search, filtering, and batch email
6. **Exports** clean contact data for Power BI or direct use

---

## Data sources

All five load live from Google Sheets at runtime — there are no local CSVs and
no upload step. URLs live in the `SHEETS` dict at the top of
`Merge_ped_contacts_v2.py`. The sidebar has a **Data sources** panel with a
clickable link and row count for each.

| Source | Owner | Notes |
|---|---|---|
| Analyst assignments | SBB | The spine. The only source carrying `PED_NO`. |
| District contacts | SBB | Joins on `PED_NO` directly. |
| Charter directory — *All Charter Schools* | CSD (read-only) | Administrator (charter rep), authorizer, contract term, enrollment cap, grades, phone, street address |
| Charter directory — *Business Managers* | CSD (read-only) | Business manager name, title, email, phone, mailing address |
| Charter name overrides | SBB | Manual `charter name → PED_NO` escape hatch |

**The charter workbook belongs to the Charter Schools Division.** We have read
access only. If CSD retires a tab or changes its `gid`, patch the gid in `SHEETS`
and move on — the app will keep serving districts in the meantime and will say
in the sidebar which tab failed to load.

### Why the two charter tabs are matched separately

Neither charter tab has a PED number, so both are name-matched against the
assignment roster **independently**. Chaining them (business managers → directory
→ assignments) would compound the error of two fuzzy hops, and a miss in one tab
would silently suppress the other. Matching each to the spine means one fuzzy hop
each, and a school can match one tab without the other — which is what the
**Partial** state means.

---

## Quick start

```bash
pip install -r requirements.txt
streamlit run Merge_ped_contacts_v2.py
```

No data prep required. Use **Refresh data** in the sidebar to clear the one-hour
cache and re-pull.

---

## Matching

### The tiers

Each charter row is resolved in this order, stopping at the first hit:

1. **Override** — the name appears in the overrides sheet. Skips everything else.
2. **Exact** — normalised name (or a variant) maps to exactly one PED number.
3. **Squash** — normalised names match once all spacing is removed. Rescues
   spacing-only differences like `Alma d'Arte` vs `Alma d' arte`.
4. **Fuzzy** — `token_set_ratio`, then `partial_ratio`, above their thresholds.

Anything unresolved is written to the **Charter match log** with a reason.

### Name normalisation

CSD keeps editorial notes inside the school-name column, so normalisation strips
them before matching:

- `(formerly known as X)`, `(CLOSED)`, `(opening 2025-26)` — note-parentheticals removed
- `(The) ASK Academy` vs `ASK Academy (The)` — parens dropped, articles removed
- En/em dashes, apostrophes, and punctuation flattened
- Boilerplate removed: *public, charter, school, academy, district, high, prep, learning, center…*

Status words aren't just discarded — `charter_status()` captures **Closed** /
**Opening** / **Inactive** and shows them as a badge on the contact card.

### The ambiguity guard — important

Normalisation strips `academy` and `school`, which leaves some names very thin.
`Explore Academy` normalises to just `explore`, and `token_set_ratio` scores that
at **100 against all three Explore campuses simultaneously**.

`process.extractOne()` resolves such ties by returning an arbitrary winner. Earlier
versions of this app did exactly that, which meant a tie could silently attach a
school's contacts to a sibling campus with full confidence and no warning.

The current code collects **every** roster name sharing the top score and resolves
them by PED number:

- All tied names point to one PED → accept (they're variants of one school)
- Tied names span multiple PEDs → **refuse**, and log
  `ambiguous — 3 schools tied at 100: 557-001, 581-001, 586-001`

Refusals are deliberate. Fix them once in the overrides sheet rather than by
lowering thresholds. Fuzzy matching is also skipped entirely for normalised names
under 5 characters, which are too thin to match safely.

### Fan-out guard

If two source rows resolve to the same PED number, only the highest-scoring one is
kept; the loser is marked `duplicate — another row matched this PED first` in the
log. Without this, one LEA would appear twice in every export.

### Merged cells

CSD vertically merges the name cell for schools listed with two grade bands — Explore
Academy Albuquerque spans two rows (K-5 and 6-12). A CSV export writes the merged
value on the first row only, leaving a nameless orphan row holding half the contact
data. Rows are stitched back together by forward-filled name, taking the first
non-empty value in each column.

### Settings (sidebar → Charter matching)

- **Token-set threshold** (80–100, default 92) — higher is stricter
- **Partial threshold** (90–100, default 96)
- **Exact matches only** — disables fuzzy entirely; everything else goes to the log

---

## Match states

| State | Meaning |
|---|---|
| **Matched** | District joined on PED_NO, or charter found in at least one charter tab |
| **Partial** | Charter found in exactly one of the two charter tabs — half-populated |
| **Unmatched** | No contact data resolved |

Filter to either **Only unmatched** or **Only partial** in the sidebar.

---

## Features

- **Search** — full-text across every field
- **Filters** — analyst, supervisor, LEA type, unmatched, partial
- **Contact cards** — analyst, charter rep / superintendent, business manager,
  charter profile (contract term, enrollment cap, grades), address
- **Batch email** — by analyst, specific LEAs, or the current filtered view;
  copy-paste box plus a `mailto:` link
- **Exports** — filtered results, all contacts, and the match log

Email addresses are validated before use. CSD parks prose like *"Please contact CSD"*
in the email column, and occasionally leaves a trailing comma; neither reaches a
`mailto:` link.

---

## Troubleshooting

**A charter tab shows 0 rows / a sidebar warning about loading**
CSD changed the `gid` or retired the tab. Open the workbook via the sidebar
**Data sources** panel, read the new gid from the URL, update `SHEETS`.

**"Column 'X' not found in the assignments sheet"**
The column almost certainly still exists under an edited header. Headers drift
constantly — `Analyst Reports To` became `Analyst Manager` (Jul 2026), and
`Analyst` became `Analyst \n(T)=Temporary Analyst` (Aug 2026), newline included.
The warning prints the raw column list; add the new text to the candidate list in
`_prep_assignments()`.

Headers are bound **most specific first**, each removed from the pool before the
next lookup, so plain `Analyst` can safely use substring matching without
swallowing `Analyst Email`. Preserve that ordering when adding candidates. When a
header resolves despite drifted text, the Data sources panel notes what bound to what.

**A school matched to the wrong campus**
Check the match log for its match method. Add it to the overrides sheet — that's
faster and safer than adjusting thresholds, which affects every school.

**A school won't match at all**
Look up its reason in the match log. `below threshold` usually means a
transliteration difference (`Dził Ditł'ooí` vs `Dzit Dit Lool` scores ~92, right at
the default cut). Either add it to overrides or add a canonical form to
`_KNOWN_RENAMES`.

**Email link doesn't work**
Some clients cap `mailto:` recipients. Use the copy-paste box instead, and keep
batches under ~50.

---

## File structure

```
├── Merge_ped_contacts_v2.py       # Main app
├── requirements.txt               # Python dependencies
├── README.md                      # This file
├── 300 DPI NM PED Logo JPEG.jpg   # PED branding (optional)
└── .streamlit/config.toml         # Streamlit config
```

Dependencies: `streamlit`, `pandas`, `rapidfuzz`, `unidecode`, `pillow`, `openpyxl`.

---

## Customization

**Colors** — edit the CSS block near the top:
primary teal `#245d62`, dark teal `#1a474b`, gold `#edc872`, coral `#c64c43`.
Coral is reserved for the alert/unmatched state; gold is used for hairline dividers.

**Known renames** — add stable name reorganisations to `_KNOWN_RENAMES`.

**Logo** — replace `300 DPI NM PED Logo JPEG.jpg` (90px height recommended).

---

## Power BI

Export **All contacts**, then Get Data → Text/CSV in Power BI Desktop. Build
relationships on `PED_NO` or `Analyst`. The export includes match method and score
columns, so match quality can be audited downstream.

---

Built for the School Budget Bureau's quarterly compliance review workflow.