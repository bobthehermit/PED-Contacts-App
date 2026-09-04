# PED Contacts Manager

**New Mexico PED - School Budget Bureau**

An intelligent contact management and deduplication tool for school business officials across New Mexico's 216 school districts and charter schools. Merges data from multiple sources, handles name variations, and maps contacts to their assigned analyst.

## Overview

This tool solves the challenge of maintaining accurate contact information across a decentralized school system with naming inconsistencies, charter/district reorganizations, and multiple data sources. It:

1. **Loads** assignment data, district information, and charter school data
2. **Normalizes** school names and removes duplicates using fuzzy matching
3. **Maps** contacts to assigned analysts and supervisors
4. **Identifies** unmatched or problematic records
5. **Provides** search, filtering, and batch email capabilities
6. **Exports** clean contact data for Power BI or direct use

## Quick Start

### 1. Install Dependencies
```bash
pip install -r requirements.txt
```

Dependencies include:
- `streamlit` - Web app framework
- `pandas` - Data manipulation
- `rapidfuzz` - Fuzzy string matching
- `unidecode` - Unicode normalization
- `pillow` - Image handling for PED logo

### 2. Data Sources

The app reads live from Google Sheets (URLs in `SHEETS` at the top of the script); there are no local CSVs to prepare.

- **Assignments** (SBB) — the roster, one row per LEA: `PED NO`, `DISTRICT, STATE, OR LOCAL CHARTER`, `DISTRICT/CHARTER NAME`, `Analyst`. As of Sep 2026 the tab also carries a small **Contacts** side table to the right of the roster (currently columns F–J, header on row 4) with one row per analyst: `Analyst`, `Analyst Email`, `Analyst Phone`, `Analyst Manager`, `Analyst Manager Email`. The app finds that table by its headers, not its position, and joins it to the roster by analyst name. Cells like `Adrianna Benavidez (T)/Lukas Lowery-Ross` resolve both people and show their details separated by ` / `.
- **Districts** (SBB) — district contact data keyed on `PED NO`.
- **Charter directory** (Charter Schools Division, read-only) — the "All Charter Schools" tab of the NM Charter School Directory workbook. Joined on `PED NO`, with name matching as a fallback.
- **Overrides** (SBB) — charter name → PED number, for rows the fallback can't resolve.

### 3. Launch the App
```bash
streamlit run Merge_ped_contacts_v2.py
```

This opens the app in your browser with your contact data pre-loaded.

## Features

### Smart Name Matching
- Handles name variations (e.g., "Rio Rancho Public Schools" vs "Rio Rancho Scools" vs "RRPS")
- Removes common articles and suffixes (the, a, an, school, academy, district, etc.)
- Ignores punctuation, spacing, and Unicode differences
- Fuzzy matching with configurable thresholds for ambiguous cases
- Knows about school reorganizations (e.g., schools formerly known as X)

### Contact Organization
- Maps each LEA to its assigned analyst and supervisor
- Tracks match confidence (exact vs. fuzzy)
- Flags unmatched schools for manual review
- Consolidates contact info into single record per LEA

### Search & Filtering
- Full-text search across all contact fields
- Filter by analyst or supervisor
- Toggle view to show only unmatched records
- One-click reset of all filters

### Batch Email
- Generate email links for multiple recipients at once
- Choose by analyst, specific LEAs, or current filtered view
- Compose subject line and body
- Copy-paste email addresses or open email client directly

### Exports
- **Filtered Results** - Currently visible contacts as CSV
- **All Contacts** - Complete merged dataset with analyst mappings
- **Match Log** - Details on how each school was matched, useful for QA

## Configuration

### Matching Settings (Sidebar)

**Token Set Threshold** (80-100, default 92)
- Higher = stricter matching, fewer false positives
- Lower = more lenient, may match unrelated schools
- Use 95+ for very strict mode

**Partial Match Threshold** (90-100, default 96)
- Controls how close school names must be
- Higher values require more exact matches

**Strict Mode**
- When enabled, only exact matches are accepted
- All fuzzy matches flagged as unmatched for manual review
- Useful for critical QA passes

### Data Sources

All four sources are Google Sheets, listed with owner and row count in the sidebar's **Data sources** expander. The **Refresh data** button clears the one-hour cache.

## How to Use

1. **Start the app** and wait for data to load
2. **Review metrics** - See total LEAs, matched count, unmatched count
3. **Filter by analyst or supervisor** using the sidebar
4. **Search** for specific schools or contacts using the search box
5. **Select an LEA** from the dropdown to view detailed contact info
6. **Generate batch emails** if sending notifications to multiple contacts
7. **Export data** when ready to use in Power BI or send to stakeholders

### Typical Workflows

**Find all unmatched schools:**
1. Check "Only unmatched" in filters
2. Review the filtered list
3. Examine match log to understand why they didn't match
4. Manually update source data if needed

**Send analyst-wide email:**
1. Filters → Select specific analyst
2. Batch Email → By analyst → Choose analyst
3. Compose subject and body
4. Click "Generate Email Link"
5. Opens your email client with all recipients pre-filled

**Export clean data for Power BI:**
1. Apply any desired filters
2. Click "⬇️ All Contacts"
3. CSV downloads with analyst mappings included
4. Import to Power BI for further analysis

## File Structure
```
├── Merge_ped_contacts_v2.py       # Main app
├── requirements.txt               # Python dependencies
├── README.md                       # This file
├── 300 DPI NM PED Logo JPEG.jpg   # PED branding (optional)
└── .streamlit/config.toml         # Theme
```

## Troubleshooting

**"Column 'Analyst Email' not found in the assignments sheet" (or similar)**
- The analyst contact columns live in the Contacts side table on the Assignments tab. The app looks for a header row containing `Analyst` with `Analyst Email` beside it, and a blank column between it and the roster. If the table was renamed or the gap column removed, the app can't see it — check the headers, or add the new header text to the candidates in `_attach_analyst_contacts()`.

**"Analyst name(s) on the roster with no row in the Contacts side table"**
- The name in the roster's Analyst column doesn't match any name in the Contacts table (after ignoring case, punctuation and the `(T)` marker; a unique surname also matches). Fix the spelling in the sheet.

**Too many/too few matches**
- Adjust token set and partial match thresholds in Matching Settings
- Try strict mode to see which records need manual review
- Check match log to understand the matching logic

**Email link doesn't work**
- Some email clients don't support `mailto:` links with many recipients
- Use the "Copy email addresses" option to paste into your email client manually
- Limit batch emails to under 50-100 recipients if possible

**School name not matching expected school**
- Check match log—see if it matched to something else
- Review source data for spelling/formatting issues
- If the issue persists, add to `KNOWN_RENAMES` dictionary in code

## Customization

### Update PED Branding
Replace `300 DPI NM PED Logo JPEG.jpg` with your own logo (90px height recommended)

### Adjust Colors
Edit the CSS in the `<style>` section to match your organization's colors:
- Primary: `#245d62` (teal-blue)
- Secondary: `#c64c43` (coral-red), `#f4784e` (orange), `#edc872` (gold)

### Known School Renames
Add common school reorganizations to `KNOWN_RENAMES` dictionary in the code to improve matching accuracy

## Integration with Power BI

After exporting "All Contacts":
1. Open Power BI Desktop
2. Get Data → Text/CSV
3. Select the exported CSV
4. Load into your data model
5. Create relationships by PED_NO or analyst
6. Build dashboards on contact distribution, coverage, etc.

---

Built for the School Budget Bureau's quarterly compliance review workflow.