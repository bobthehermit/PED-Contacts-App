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

### 2. Prepare Your Data

Place three CSV files in the app directory (or upload them when prompted):

- **assignments.csv** - School-to-analyst mappings
  - Columns: `School_Name`, `Analyst`, `Analyst Reports To` (supervisor)
  
- **districts.csv** - Public school district contact data
  - Columns: `PED_NO`, `District_Name`, `Contact_Name`, `Email`, `Phone`, etc.
  
- **charters.csv** - Charter school contact data
  - Columns: `PED_NO`, `Charter_Name`, `Contact_Name`, `Email`, `Phone`, etc.

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

Toggle between:
- **Local CSV files** - Pre-bundled CSVs in the app folder (faster, always available)
- **Upload CSVs** - Upload fresh files each session (useful for testing new data)

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
├── assignments.csv                # Local data (if bundled)
├── districts.csv                  # Local data (if bundled)
└── charters.csv                   # Local data (if bundled)
```

## Troubleshooting

**"Some CSV files are missing"**
- Switch to "Upload CSVs" mode in sidebar if you don't have bundled files
- Or place assignment, districts, and charters CSVs in the app folder

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