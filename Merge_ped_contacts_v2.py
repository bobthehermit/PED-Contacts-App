# Merge_ped_contacts_v2.py — PED Contacts Manager (Streamlined)
# Merges analyst assignments with district/charter contact sheets.
# Charter matching uses fuzzy name matching (no PED# in charter sheets).
# Districts match on PED_NO directly.
#
# Design pass (Jun 2026): de-emojified, restyled to "institutional clarity"
#   — teal as the structural accent, gold as hairline dividers, coral
#   reserved strictly for the unmatched/alert state. Logic unchanged.
#
# Patch (Jul 2026): the assignments sheet's "Analyst Reports To" header was
#   renamed to "Analyst Manager" upstream (with two new sibling columns,
#   "Analyst Manager Email" / "Analyst Manager Phone"), which crashed the
#   app with a KeyError. _prep_assignments() now resolves analyst-related
#   headers the same fuzzy way PED_NO/LEA_TYPE/LEA_NAME already were,
#   instead of requiring an exact literal match.
#
# Patch (Aug 2026): CHARTER SOURCE REPLACED. The old single charter contact
#   sheet was retired. Charters now come from the Charter Schools Division's
#   "New Mexico Charter School Directory 2026-27" workbook, across TWO tabs:
#     • "All Charter Schools"  — authorizer, contract term, enrollment cap,
#       grades, phone, street address, Administrator (the charter rep) + email
#     • "Business Managers"    — mailing address, business manager name,
#       title, email, phone
#   Neither tab carries a PED number, so both are matched by name against the
#   assignments roster independently (one fuzzy hop each) rather than chaining
#   BM -> Directory -> assignments, which would compound match error.
#
#   Three hazards this patch specifically defends against:
#     1. CSD keeps editorial notes inside the school-name column — "(CLOSED)",
#        "(formerly known as X)", "(opening 2025-26)", "(The)". normalize_name()
#        now strips note-parentheticals before matching, and charter_status()
#        captures the status so it is surfaced instead of silently mangled.
#     2. The two tabs disagree on names ("(The) ASK Academy" vs "ASK Academy
#        (The)"; "Explore Academy Albuquerque" vs plain "Explore Academy").
#     3. AMBIGUOUS FUZZY TIES. token_set_ratio scores a short name like
#        "explore" at 100 against explore albuquerque / las cruces / rio rancho
#        simultaneously. The previous code used process.extractOne(), which
#        returns an arbitrary winner among ties and passed the len(ps)==1
#        guard — i.e. it would silently attach contacts to the WRONG school.
#        _best_fuzzy() now detects ties and refuses rather than guessing;
#        refusals are written to the match log with reason="ambiguous" so
#        they can be resolved once in the overrides sheet.

# Patch (Aug 25 2026): CSD RESTRUCTURED THE WORKBOOK AGAIN, ~24h after the
#   patch above. The "Business Managers" tab was deleted and folded into
#   "All Charter Schools" (hence the HTTPError on its gid), and the surviving
#   tabs are now All Charter Schools / Changes / Authorizers.
#
#   The important part is a gift, not a loss: the consolidated tab now carries
#   a PED NO column. Charters therefore join on PED_NO exactly the way
#   districts always have. Name matching is demoted to a FALLBACK for rows
#   whose PED cell is blank or absent from the roster — the whole
#   normalise/fuzzy/ambiguity apparatus below is kept, but on most runs it
#   should never fire. If it does, the match log says so.
#
#   The tab also brings second head administrators, governing board
#   presidents, and attorneys, and it now vertically merges the
#   Authorizer/Charter School/Acronym/PED cells for schools with two campuses
#   (ACES, AIMS), so the continuation row carries a real second address and
#   phone rather than just a grade band. Those alternates are preserved
#   instead of being dropped by the collapse.

# Patch (Sep 4 2026): SBB ASSIGNMENTS TAB RESTRUCTURED. The per-row analyst
#   contact columns (Analyst Email / Phone / Manager / Manager Email) were
#   removed from the roster and replaced by a small "Contacts" side table
#   sitting to the right of the roster on the same tab (cols F-J, header on
#   row 4, one row per analyst). Read as a single CSV, that region has no
#   header on row 1, so the app saw "Unnamed: 4..9" and warned that every
#   analyst column was missing.
#
#   _split_assignments_tab() now locates the side table by content (a row
#   containing an "Analyst" cell with "Analyst Email" beside it, preceded by
#   a blank gap column), carves it off, and _prep_assignments() joins it back
#   onto the roster by analyst NAME. Multi-analyst cells such as
#   "Adrianna Benavidez (T)/Lukas Lowery-Ross" resolve each person and join
#   their details with " / ". If the side table is ever removed and the
#   inline columns come back, the old inline path still works unchanged.

import re, os, base64, urllib.parse
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import streamlit as st
from unidecode import unidecode
from rapidfuzz import process, fuzz
from PIL import Image

# ─── Page config ─────────────────────────────────────────────────────
st.set_page_config(
    page_title="PED Contacts Manager",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ─── Brand CSS ───────────────────────────────────────────────────────
# NMPED palette
#   Primary teal: #245d62   Dark teal: #1a474b
#   Gold:         #edc872   Coral:     #c64c43
#   (Light yellow #fef0c3 intentionally retired from panels.)
st.markdown("""
<style>
/* Layout — a comfortable max width reads more intentional than full-bleed.
   Delete the max-width line to go back to edge-to-edge wide mode. */
.block-container { padding-top: 1.1rem !important; max-width: 1180px; }

/* Headings */
h1, h2, h3, h4 { color: #245d62; font-weight: 600; }

/* Custom masthead */
.ped-eyebrow {
    font-size: 11px; letter-spacing: .12em; text-transform: uppercase;
    color: #7a8a86; font-weight: 600;
}
.ped-title {
    font-family: Georgia, "Times New Roman", serif;
    font-size: 2rem; color: #245d62; font-weight: 600;
    margin: .15rem 0 .6rem; line-height: 1.1;
}
.ped-rule { display: flex; height: 3px; margin-bottom: 1.3rem; }
.ped-rule .g { width: 46px; background: #edc872; }
.ped-rule .t { flex: 1; background: #245d62; }

/* Section label inside contact cards — gold hairline */
.section-label {
    font-size: 11px; letter-spacing: .08em; text-transform: uppercase;
    color: #245d62; font-weight: 600;
    padding-bottom: 6px; border-bottom: 1px solid #edc872;
    margin: 16px 0 10px;
}

/* Status pills + badges */
.pill { font-size: 11px; font-weight: 600; padding: 3px 10px; border-radius: 20px; }
.pill-matched   { color: #1a474b; background: #e1efe9; }
.pill-unmatched { color: #993c1d; background: #faece7; }
.pill-partial   { color: #7a5c12; background: #fbf3dd; }
.badge {
    font-size: 12px; color: #5f5e5a; background: #f1efe8;
    padding: 3px 9px; border-radius: 6px;
}
.badge-alert { color: #993c1d; background: #faece7; font-weight: 600; }

/* Links — teal, not coral (coral is reserved for alerts) */
a { color: #245d62; text-decoration: none; }
a:hover { color: #1a474b; text-decoration: underline; }

/* Download buttons */
.stDownloadButton button {
    width: 100%; background: #245d62; color: #fff !important; border: none;
}
.stDownloadButton button:hover { background: #1a474b; }
.stDownloadButton button p,
.stDownloadButton button span { color: #fff !important; }

/* Sidebar multiselect tags */
[data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] {
    background: #245d62 !important;
}
[data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] span { color: #fff !important; }
[data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] svg  { fill: #fff !important; }

/* Expander — lighter frame */
[data-testid="stExpander"] { border: 0.5px solid #ececec; border-radius: 8px; }

/* Pagination buttons */
.pagination-row button { min-width: 52px; }
</style>
""", unsafe_allow_html=True)

# ─── Logo ────────────────────────────────────────────────────────────
LOGO_PATH = Path(__file__).parent / "300 DPI NM PED Logo JPEG.jpg"
LOGO_LINK = "https://web.ped.nm.gov/bureaus/school-budget-bureau/"

def _load_logo():
    if not LOGO_PATH.exists():
        return None
    try:
        buf = BytesIO()
        Image.open(LOGO_PATH).save(buf, format="PNG")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return (
            f'<a href="{LOGO_LINK}" target="_blank">'
            f'<img src="data:image/png;base64,{b64}" '
            f'style="max-height:90px;height:auto;max-width:100%"></a>'
        )
    except Exception:
        return None

logo = _load_logo()
if logo:
    st.sidebar.markdown(logo, unsafe_allow_html=True)
st.sidebar.caption("School Budget Bureau")

# ═════════════════════════════════════════════════════════════════════
# HELPERS
# ═════════════════════════════════════════════════════════════════════

def _clean(x):
    """Return stripped string or empty."""
    return "" if pd.isna(x) else str(x).strip()


def ped_canonical(x: str) -> str:
    """Normalize PED numbers → XXX-XXX."""
    if pd.isna(x) or str(x).strip() == "":
        return ""
    s = str(x).strip()
    if "-" in s:
        left, right = s.split("-", 1)
        try:
            return f"{int(left):03d}-{int(right):03d}"
        except ValueError:
            return s
    try:
        return f"{int(s):03d}-000"
    except ValueError:
        return s


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$")

def _valid_email(x) -> str:
    """Return a usable address, or '' — CSD parks prose like 'Please contact
    CSD' in the email column, and occasionally leaves a trailing comma/period."""
    s = _clean(x).strip(" ,;.")
    if not s:
        return ""
    # some cells hold two addresses separated by , or ; — take the first valid
    for part in re.split(r"[,;]\s*", s):
        p = part.strip()
        if _EMAIL_RE.match(p):
            return p
    return ""


# ── Name normalisation (for charter fuzzy matching) ──────────────────
_ARTICLES = re.compile(r"\b(the|a|an)\b")
_SUFFIXES = re.compile(
    r"\b(public|charter|school|schools|academy|district|high|middle|"
    r"elementary|prep|preparatory|learning|center|centers)\b"
)

# Parenthetical groups that are CSD's editorial notes, not part of the name.
# Anything else in parens (e.g. "(DEAP)") is kept, since it may disambiguate.
_NOTE_PAREN = re.compile(
    r"\([^)]*\b(formerly|formally|previously|renamed|closed|close|closing|"
    r"opening|opens|pending|inactive|fka|f/k/a|vote|new site)\b[^)]*\)"
)
_STATUS_WORDS = re.compile(r"\b(closed|inactive)\b")

_KNOWN_RENAMES = {
    "academy for technology and the classics the":
        "academy for technology and the classics",
    "albuquerque aviation academy formerly known as sams":
        "albuquerque aviation academy",
    "northpoint charter school formerly southwest secondary learning center":
        "northpoint charter school",
}

def normalize_light(name: str) -> str:
    """Punctuation, case, diacritics, articles, and CSD's editorial notes —
    but the words are left alone.

    'South Valley Academy' and 'South Valley Preparatory School' stay distinct
    here. That matters: the heavy pass below strips 'academy', 'charter',
    'school' and 'preparatory' as boilerplate, which is what lets
    '(The) ASK Academy' meet 'ASK Academy (The)' — but it also dissolves the
    only token separating sibling campuses, collapsing both South Valley
    schools onto 'south valley'. Match on this form first.
    """
    if pd.isna(name) or str(name).strip() == "":
        return ""
    s = unidecode(str(name)).lower()
    s = re.sub(r"\(formerly[^)]*\)", " ", s)
    s = _NOTE_PAREN.sub(" ", s)              # "(CLOSED)", "(opening 2025-26)"…
    s = re.sub(r"[()\[\]]", " ", s)          # keep the contents of other parens
    s = _STATUS_WORDS.sub(" ", s)            # bare "CLOSED" with no parens
    s = re.sub(r"[\u2010-\u2015]", " ", s)   # en/em dashes → space
    s = re.sub(r"['\u2018\u2019`]", "", s)   # d'Arte → dArte
    s = re.sub(r"[-/.,&:;#@]+", " ", s)
    s = _ARTICLES.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def normalize_name(name: str) -> str:
    """Heavy pass — light, plus boilerplate removal. High recall, low
    precision: use it only after normalize_light() has had its turn."""
    s = normalize_light(name)
    if not s:
        return ""
    s = _SUFFIXES.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _KNOWN_RENAMES.get(s, s)


def _squash(s: str) -> str:
    """Whitespace/punctuation-free key. Rescues spacing-only differences
    such as 'alma darte' vs 'alma d arte'."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def charter_status(raw_name: str) -> str:
    """Read CSD's inline note out of the name cell so it can be shown as a
    badge rather than quietly deleted by normalisation."""
    s = unidecode(_clean(raw_name)).lower()
    if re.search(r"\bclosed?\b|\bclosing\b", s):
        return "Closed"
    if re.search(r"\bopening\b|\bopens\b", s):
        return "Opening"
    if re.search(r"\binactive\b", s):
        return "Inactive"
    return ""


def _name_variants(n: str) -> set[str]:
    if not n:
        return {""}
    vs = {n}
    for word in ("the", "academy", "academies", "high"):
        vs.add(n.replace(f" {word} ", " ").strip())
        vs.add(n.replace(f" {word}", "").strip())
    vs.add(n.replace(" prep", " preparatory"))
    return {re.sub(r"\s+", " ", v).strip() for v in vs if v}


def _is_charter(code: str) -> bool:
    return _clean(code).upper() in {"SC", "LC"}

def _is_district(code: str) -> bool:
    return _clean(code).upper() in {"D", "DISTRICT"}


# Fuzzy matches shorter than this are refused outright: once normalize_name()
# has stripped "academy"/"school"/"charter", names like "taos" or "ask" are too
# thin to fuzzy-match safely. They can still land via the exact/squash tiers.
_MIN_FUZZY_LEN = 5


def _best_fuzzy(target: str, choices: list[str],
                token_cut=92, partial_cut=96):
    """Return (tied_names, score, method) — every roster name sharing the top
    score, not just one of them.

    This is the fix for the silent-mismatch bug. process.extractOne() collapses
    ties to an arbitrary winner, so a short probe like "explore" (which scores
    100 against explore albuquerque / las cruces / rio rancho simultaneously)
    would be handed one campus at random and pass the old len(ps)==1 guard.
    Returning the full tied set lets the caller decide by PED number: ties that
    all resolve to the same school are fine, ties that straddle schools are not.
    """
    if not target or not choices or len(target) < _MIN_FUZZY_LEN:
        return [], 0, ""
    for scorer, cut, label in ((fuzz.token_set_ratio, token_cut, "token_set"),
                               (fuzz.partial_ratio, partial_cut, "partial")):
        hits = process.extract(target, choices, scorer=scorer, limit=25)
        if not hits:
            continue
        top = hits[0][1]
        if top < cut:
            continue
        return [h[0] for h in hits if h[1] >= top - 0.01], top, label
    return [], 0, ""


# ═════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════════════

# The charter workbook is owned by the Charter Schools Division. We have read
# access only, so if a tab moves or is retired, patch the gid here and move on.
CHARTER_BOOK = "1_Uws15oI1t0K4ccdiIiX2cbixqHglPBuopdEEWbkXFM"

SHEETS = {
    "districts":   "https://docs.google.com/spreadsheets/d/1vkzVbwmg3LktPWlxK-SIi28hSIaP2YIG_wnp2FgWPYE/export?format=csv&gid=0",
    "assignments": "https://docs.google.com/spreadsheets/d/1uZY1Ep9jMpachr7MtBBy5Rwi25iVv80jp0X3i_e1ezg/export?format=csv&gid=1629654616",
    "overrides":   "https://docs.google.com/spreadsheets/d/1K-Hh7p9I30wjjumTeKy44D_VZnGmL87oJuNEYLywt5w/export?format=csv&gid=0",
    # NM Charter School Directory 2026-27 (Charter Schools Division).
    # The separate Business Managers tab (gid 352262694) was deleted by CSD on
    # 2026-08-25 and folded into this one.
    "charter_directory": f"https://docs.google.com/spreadsheets/d/{CHARTER_BOOK}/export?format=csv&gid=1811433778",   # All Charter Schools
}

def _edit_url(export_url: str) -> str:
    """Turn a CSV-export URL into one a person can actually open in a browser."""
    m = re.search(r"/d/([^/]+)/export\?format=csv(?:&gid=(\d+))?", export_url)
    if not m:
        return export_url
    doc, gid = m.group(1), m.group(2) or "0"
    return f"https://docs.google.com/spreadsheets/d/{doc}/edit#gid={gid}"


# Sidebar "Data sources" panel. Owner is recorded because it determines what
# happens when one breaks: SBB sheets we can fix at source, CSD sheets we can
# only re-point at.
SOURCE_INFO = [
    ("Analyst assignments", "assignments",        "SBB"),
    ("District contacts",   "districts",          "SBB"),
    ("Charter directory — All Charter Schools", "charter_directory", "CSD (read-only)"),
    ("Charter name overrides", "overrides",       "SBB"),
]


@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_sheets():
    """Load every sheet; return (assign, districts, directory, overrides,
    notes, timestamp).

    The charter tab loads defensively: if CSD retires or re-gids it — which
    has now happened twice — the district side of the app must keep working
    rather than blanking the whole screen. Failures come back as a note shown
    in the sidebar naming the key to patch.
    """
    ts = datetime.now().strftime("%b %d %Y, %I:%M %p")
    notes: list[str] = []

    # header=None: the assignments tab now holds TWO tables (roster + analyst
    # contacts side table), so the first row is not a header for the whole
    # grid. _split_assignments_tab() works out where each one lives.
    raw_assign = pd.read_csv(SHEETS["assignments"], dtype=str, header=None).fillna("")
    assign, analyst_contacts = _split_assignments_tab(raw_assign)
    districts = pd.read_csv(SHEETS["districts"],   dtype=str).fillna("")

    def _try(key, label):
        try:
            df = pd.read_csv(SHEETS[key], dtype=str).fillna("")
            if df.empty:
                notes.append(f"{label} loaded but is empty.")
            return df
        except Exception as exc:
            notes.append(f"{label} could not be loaded ({type(exc).__name__}). "
                         f"Check the gid in SHEETS['{key}'].")
            return pd.DataFrame()

    directory = _try("charter_directory", "Charter directory tab")

    try:
        overrides = pd.read_csv(SHEETS["overrides"], dtype=str).fillna("")
        col_map = {}
        for c in overrides.columns:
            cl = c.strip().lower()
            if "name" in cl:
                col_map[c] = "CHARTER_NAME"
            elif "ped" in cl:
                col_map[c] = "PED_NO"
        overrides = overrides.rename(columns=col_map)
        if "CHARTER_NAME" not in overrides.columns or "PED_NO" not in overrides.columns:
            overrides = pd.DataFrame(columns=["CHARTER_NAME", "PED_NO"])
        overrides["PED_NO"] = overrides["PED_NO"].apply(ped_canonical)
        overrides = overrides[
            overrides["CHARTER_NAME"].str.strip().ne("")
            & overrides["PED_NO"].str.strip().ne("")
        ]
    except Exception:
        overrides = pd.DataFrame(columns=["CHARTER_NAME", "PED_NO"])

    return assign, analyst_contacts, districts, directory, overrides, notes, ts


# ── Column normalisation helpers ─────────────────────────────────────

def _hdr_key(s: str) -> str:
    """Comparison key for a header: lowercase, alphanumerics only.
    CSD writes headers with trailing colons ('Charter School:'), so a plain
    case-insensitive compare misses them."""
    return re.sub(r"[^a-z0-9]", "", str(s).lower())


def _find_col(df: pd.DataFrame, candidates: list[str], contains: bool = False):
    """Return the first matching column name, or None.

    Exact (punctuation-insensitive) match is tried across all candidates
    first; only then, if contains=True, a substring match. That ordering
    matters — 'Name' must bind to 'Name:' before it is allowed to bind to
    something merely containing 'name'.
    """
    keyed = {_hdr_key(c): c for c in df.columns}
    for cand in candidates:
        k = _hdr_key(cand)
        if k in keyed:
            return keyed[k]
    if contains:
        for cand in candidates:
            k = _hdr_key(cand)
            if not k:
                continue
            for hk, orig in keyed.items():
                if k in hk:
                    return orig
    return None


def _split_assignments_tab(raw: pd.DataFrame):
    """Split the raw assignments grid into (roster, analyst_contacts).

    The tab is read with header=None, so `raw` is a plain cell grid with
    integer column labels. The roster starts at A1 as before. The analyst
    contacts side table is found by CONTENT, not position: the first cell
    whose header key is exactly "analyst", with an "analystemail" cell within
    the next three columns, and a blank cell immediately to its left (the
    gap column that separates the two tables). Anchoring on content means a
    future move from F4 to, say, H2 still works.

    Returns the roster with row 0 promoted to headers, and the side table
    with its own header row promoted — or an empty frame if none was found,
    in which case _prep_assignments() falls back to the old inline columns.
    """
    if raw.empty:
        return raw, pd.DataFrame()

    hdr_row = start_col = None
    for r in range(min(len(raw), 60)):
        row = raw.iloc[r].tolist()
        for c, cell in enumerate(row):
            if _hdr_key(cell) != "analyst" or c == 0:
                continue
            if _clean(row[c - 1]):
                continue                       # no gap column: part of the roster
            nearby = {_hdr_key(x) for x in row[c + 1:c + 4]}
            if "analystemail" in nearby:
                hdr_row, start_col = r, c
                break
        if hdr_row is not None:
            break

    if hdr_row is None:
        # Old layout (or side table not found): row 0 is the whole header.
        roster = raw.iloc[1:].copy()
        roster.columns = raw.iloc[0].tolist()
        roster = roster.loc[:, [bool(_clean(c)) for c in roster.columns]]
        return roster.reset_index(drop=True), pd.DataFrame()

    # Side table extends right while the header cells stay non-empty…
    end_col = start_col
    while end_col + 1 < raw.shape[1] and _clean(raw.iat[hdr_row, end_col + 1]):
        end_col += 1
    # …and down until the first blank Analyst cell.
    body = raw.iloc[hdr_row + 1:, start_col:end_col + 1].copy()
    body.columns = [_clean(c) for c in raw.iloc[hdr_row, start_col:end_col + 1]]
    first_col = body.columns[0]
    stop = body[first_col].map(_clean).eq("")
    if stop.any():
        body = body.iloc[: int(stop.to_numpy().argmax())]
    contacts = body.reset_index(drop=True)

    # Roster: everything left of the side table with a real header in row 0,
    # keeping only rows that have something in those columns.
    roster = raw.iloc[1:, :start_col].copy()
    roster.columns = raw.iloc[0, :start_col].tolist()
    roster = roster.loc[:, [bool(_clean(c)) for c in roster.columns]]
    roster = roster[roster.map(_clean).ne("").any(axis=1)]
    return roster.reset_index(drop=True), contacts


def _person_key(s: str) -> str:
    """Comparison key for an analyst name: diacritics, case, punctuation and
    the '(T)' temporary marker stripped. 'Sarah Rivera-Benavidez (T)' and
    'sarah rivera benavidez' collide, which is the point."""
    s = unidecode(_clean(s)).lower()
    s = re.sub(r"\(\s*t\s*\)", " ", s)
    return re.sub(r"[^a-z]", "", s)


def _split_people(cell: str) -> list[str]:
    """'Adrianna Benavidez (T)/Lukas Lowery-Ross' -> both names, in order."""
    parts = re.split(r"\s*(?:/|&|\band\b|;)\s*", _clean(cell))
    return [p for p in (re.sub(r"\(\s*t\s*\)", "", p, flags=re.I).strip()
                        for p in parts) if p]


def _attach_analyst_contacts(df: pd.DataFrame, contacts: pd.DataFrame):
    """Fill Analyst Email / Phone / Reports To / Manager Email on the roster
    from the side table, keyed on analyst name. Only fills columns the roster
    does not already carry inline. Returns (df, filled_cols, unresolved)."""
    if contacts is None or contacts.empty or "Analyst" not in df.columns:
        return df, [], []

    # Bind the side table's headers with the same tolerance as everything else.
    spec = [
        ("Analyst",               ["Analyst", "Analyst Name", "Name"], False),
        ("Analyst Email",         ["Analyst Email", "Email"], True),
        ("Analyst Phone",         ["Analyst Phone", "Phone"], True),
        ("Analyst Manager Email", ["Analyst Manager Email", "Manager Email",
                                   "Supervisor Email"], True),
        ("Analyst Manager Phone", ["Analyst Manager Phone", "Manager Phone"], True),
        ("Analyst Reports To",    ["Analyst Manager", "Analyst Reports To",
                                   "Manager", "Supervisor", "Reports To"], True),
    ]
    ct = contacts.copy()
    taken: set[str] = set()
    for canon, cands, contains in spec:
        pool = ct.drop(columns=[c for c in taken if c in ct.columns], errors="ignore")
        col = _find_col(pool, cands, contains=contains)
        if col:
            taken.add(canon)
            ct = ct.rename(columns={col: canon})
    if "Analyst" not in ct.columns:
        return df, [], []

    fields = [c for c, _, _ in spec[1:] if c in ct.columns and c not in df.columns]
    if not fields:
        return df, [], []

    by_key: dict[str, pd.Series] = {}
    by_last: dict[str, list[pd.Series]] = {}
    for _, r in ct.iterrows():
        k = _person_key(r["Analyst"])
        if not k:
            continue
        by_key[k] = r
        toks = re.findall(r"[a-z]+", unidecode(_clean(r["Analyst"])).lower())
        if toks:
            by_last.setdefault(toks[-1], []).append(r)

    unresolved: set[str] = set()

    def _lookup(name: str):
        r = by_key.get(_person_key(name))
        if r is not None:
            return r
        toks = re.findall(r"[a-z]+", unidecode(name).lower())
        cands = by_last.get(toks[-1], []) if toks else []
        if len(cands) == 1:                    # unique surname is good enough
            return cands[0]
        unresolved.add(name)
        return None

    def _resolve(cell: str) -> dict[str, str]:
        found = [_lookup(n) for n in _split_people(cell)]
        out = {}
        for f in fields:
            vals = [_clean(r.get(f, "")) for r in found if r is not None]
            out[f] = " / ".join(dict.fromkeys(v for v in vals if v))
        return out

    resolved = df["Analyst"].map(_resolve)
    for f in fields:
        df[f] = resolved.map(lambda d: d.get(f, ""))
    return df, fields, sorted(unresolved)


def _prep_assignments(df: pd.DataFrame,
                      contacts: pd.DataFrame | None = None) -> pd.DataFrame:
    # Headers are bound MOST SPECIFIC FIRST, and each bound column is removed
    # from the pool before the next lookup. That ordering is load-bearing:
    # plain "Analyst" has to be allowed a substring match, because the SBB
    # sheet annotates the header inline (seen Aug 2026:
    # "Analyst \n(T)=Temporary Analyst" — a legend, plus a literal newline).
    # If "Analyst" were resolved first with substring matching on, it would
    # swallow "Analyst Email"; resolved last against what remains, it can't.
    ordered = [
        ("PED_NO",   ["PED NO", "ped no", "PED_NO", "Ped No"], False),
        ("LEA_TYPE", ["DISTRICT, STATE, OR LOCAL CHARTER", "LEA TYPE", "LEA", "TYPE"], False),
        ("LEA_NAME", ["DISTRICT/CHARTER NAME", "LEA NAME", "NAME"], False),
        ("Analyst Manager Email", ["Analyst Manager Email", "ANALYST MANAGER EMAIL"], False),
        ("Analyst Manager Phone", ["Analyst Manager Phone", "ANALYST MANAGER PHONE"], False),
        # Renamed upstream Jul 2026 from "Analyst Reports To" -> "Analyst Manager".
        ("Analyst Reports To", ["Analyst Reports To", "Analyst Manager", "Reports To",
                                "Supervisor", "Analyst Supervisor", "ANALYST REPORTS TO",
                                "ANALYST MANAGER"], False),
        ("Analyst Email", ["Analyst Email", "Analyst E-mail", "ANALYST EMAIL"], True),
        ("Analyst Phone", ["Analyst Phone", "ANALYST PHONE"], True),
        ("Analyst",       ["Analyst", "Budget Analyst", "ANALYST"], True),
    ]
    bound_notes: list[str] = []
    taken: set[str] = set()
    for canon, cands, contains in ordered:
        pool = df.drop(columns=[c for c in taken if c in df.columns], errors="ignore")
        col = _find_col(pool, cands, contains=contains)
        if col:
            if _hdr_key(col) != _hdr_key(canon):
                # Header text drifted but still resolved — worth surfacing, since
                # a silent bind to the wrong column is far worse than a noisy one.
                bound_notes.append(f"'{canon}' ← {col!r}")
            taken.add(canon)
            df = df.rename(columns={col: canon})

    # Fail loudly and usefully instead of a bare KeyError three screens away.
    required = ["PED_NO", "LEA_TYPE", "LEA_NAME"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(
            f"_prep_assignments: could not find required column(s) {missing} "
            f"in the assignments sheet. Raw columns present: {df.columns.tolist()}"
        )

    # Sep 2026: contact details moved to a side table on the same tab. Fill
    # whatever the roster no longer carries inline from there, by name.
    df, from_side, unresolved = _attach_analyst_contacts(df, contacts)
    st.session_state["_assign_side_cols"] = from_side
    st.session_state["_assign_unresolved"] = unresolved

    for optional_col in ["Analyst", "Analyst Email", "Analyst Reports To",
                          "Analyst Phone", "Analyst Manager Email"]:
        if optional_col not in df.columns:
            st.sidebar.warning(
                f"Column '{optional_col}' not found in the assignments sheet, so it "
                f"shows blank everywhere. Usually the column still exists under an "
                f"edited header — check this list and add the new text to the "
                f"candidates in _prep_assignments(): {df.columns.tolist()}"
            )
            df[optional_col] = ""
    # Manager phone was dropped from the sheet in Sep 2026; keep the column
    # for downstream code but don't nag about it.
    if "Analyst Manager Phone" not in df.columns:
        df["Analyst Manager Phone"] = ""

    st.session_state["_assign_header_notes"] = bound_notes

    df["PED_NO"]        = df["PED_NO"].apply(ped_canonical)
    df["LEA_TYPE"]      = df["LEA_TYPE"].str.strip().str.upper()
    df["LEA_NAME_LIGHT"] = df["LEA_NAME"].apply(normalize_light)
    df["LEA_NAME_NORM"]  = df["LEA_NAME"].apply(normalize_name)
    return df


def _prep_districts(df: pd.DataFrame) -> pd.DataFrame:
    col = _find_col(df, ["ped no", "district no.", "PED NO"])
    if col:
        df = df.rename(columns={col: "PED NO"})
    df["PED_NO"] = df["PED NO"].apply(ped_canonical)
    return df


def _collapse_merged_rows(df: pd.DataFrame, name_col: str) -> pd.DataFrame:
    """Rebuild rows that a vertical cell-merge split apart.

    CSD merges the name/authorizer cells for schools listed with two grade
    bands (Explore Academy Albuquerque spans two rows: K-5 and 6-12). A CSV
    export writes the merged value only on the first row and leaves the rest
    blank, so a naive read produces one usable row plus one nameless orphan
    holding half the contact data. Group on the forward-filled name and take
    the first non-empty value in each column.
    """
    if df.empty or name_col not in df.columns:
        return df
    work = df.copy()
    work["_grp"] = work[name_col].replace("", None).ffill()
    work = work[work["_grp"].notna()]
    if work.empty:
        return df.iloc[0:0]
    agg = {c: (lambda s: next((v for v in s if _clean(v)), ""))
           for c in work.columns if c != "_grp"}
    return work.groupby("_grp", sort=False).agg(agg).reset_index(drop=True)


def _prep_charter_directory(df: pd.DataFrame) -> pd.DataFrame:
    """The consolidated 'All Charter Schools' tab.

    Header binding order is load-bearing. Five of CSD's headers are substrings
    of longer ones, so binding the short name first would silently capture the
    wrong column:

        'Phone Number'                  <  'Business Manager Phone Number'
        'Head Administrator First Name' <  '2nd Head Administrator First Name'
        'Head Administrator Last Name'  <  '2nd Head Administrator Last Name'
        'Administrator Email'           <  '2nd Administrator Email'
        'Governing Board President'     <  'Governing Board President Email'

    Each longer header is therefore bound FIRST and removed from the pool
    before its shorter sibling is looked up. CSD also pads headers with
    runs of spaces and trailing blanks, which _hdr_key() flattens.
    """
    if df.empty:
        return df

    # (canonical, candidates, allow_substring) — order matters, see docstring.
    ordered = [
        ("CH_BM_PHONE",       ["Business Manager Phone Number", "Business Manager Phone"], True),
        ("CH_ADMIN2_FIRST",   ["2nd Head Administrator First Name"], True),
        ("CH_ADMIN2_LAST",    ["2nd Head Administrator Last Name"], True),
        ("CH_ADMIN2_EMAIL",   ["2nd Administrator Email"], True),
        ("CH_BOARD_EMAIL",    ["Governing Board President Email"], True),
        ("CH_BOARD_NAME",     ["Governing Board President"], True),
        ("CH_ADMIN_FIRST",    ["Head Administrator First Name"], True),
        ("CH_ADMIN_LAST",     ["Head Administrator Last Name"], True),
        ("CH_ADMIN_EMAIL",    ["Administrator Email", "Admin Email"], True),
        ("CH_BM_FIRST",       ["Business Manager First Name"], True),
        ("CH_BM_LAST",        ["Business Manager Last Name"], True),
        ("CH_BM_EMAIL",       ["Business Manager Email"], True),
        ("CH_ATTORNEY_EMAIL", ["Attorney Email"], True),
        ("CH_ATTORNEY_NAME",  ["Attorney Name"], True),
        ("CH_PED",            ["PED NO", "PED_NO", "PED Number", "PED #"], False),
        ("CH_ACRONYM",        ["Acronym"], False),
        ("CH_AUTHORIZER",     ["Authorizer"], False),
        ("CH_NAME",           ["Charter School", "School Name", "Charter Name"], False),
        ("CH_CONTRACT_TERM",  ["Contract Term"], False),
        ("CH_ENROLL_CAP",     ["Enrollment Cap"], True),
        ("CH_GRADES_AUTH",    ["Grades Authorized"], True),
        ("CH_GRADES_SERVED",  ["Grades Served"], True),
        ("CH_PHONE",          ["Phone Number", "Phone"], False),
        ("CH_ADDRESS",        ["Street Address", "Address"], False),
    ]
    taken: set[str] = set()
    for canon, cands, contains in ordered:
        pool = df.drop(columns=[c for c in taken if c in df.columns], errors="ignore")
        col = _find_col(pool, cands, contains=contains)
        if col:
            taken.add(canon)
            df = df.rename(columns={col: canon})

    if "CH_NAME" not in df.columns:
        st.sidebar.warning(
            "Charter directory tab: no school-name column found. "
            f"Raw columns: {df.columns.tolist()}"
        )
        return pd.DataFrame()

    for c in ("CH_PED", "CH_ACRONYM", "CH_ADMIN_FIRST", "CH_ADMIN_LAST",
              "CH_ADMIN2_FIRST", "CH_ADMIN2_LAST", "CH_ADMIN2_EMAIL",
              "CH_BM_FIRST", "CH_BM_LAST", "CH_BM_EMAIL", "CH_BM_PHONE",
              "CH_BOARD_NAME", "CH_BOARD_EMAIL",
              "CH_ATTORNEY_NAME", "CH_ATTORNEY_EMAIL"):
        if c not in df.columns:
            df[c] = ""

    df = _collapse_charter_rows(df)
    df = df[df["CH_NAME"].apply(_clean).ne("")]

    df["CH_PED"]    = df["CH_PED"].apply(ped_canonical)
    df["CH_STATUS"] = df["CH_NAME"].apply(charter_status)
    df["CH_NAME_LIGHT"] = df["CH_NAME"].apply(normalize_light)
    df["CH_NAME_NORM"]  = df["CH_NAME"].apply(normalize_name)

    # CSD splits people across first/last columns; the cards want one string.
    df["CH_ADMIN_NAME"]  = df.apply(
        lambda r: " ".join(filter(None, [_clean(r.get("CH_ADMIN_FIRST")),
                                         _clean(r.get("CH_ADMIN_LAST"))])), axis=1)
    df["CH_ADMIN2_NAME"] = df.apply(
        lambda r: " ".join(filter(None, [_clean(r.get("CH_ADMIN2_FIRST")),
                                         _clean(r.get("CH_ADMIN2_LAST"))])), axis=1)
    df["CH_BM_NAME"]     = df.apply(
        lambda r: " ".join(filter(None, [_clean(r.get("CH_BM_FIRST")),
                                         _clean(r.get("CH_BM_LAST"))])), axis=1)

    for c in ("CH_ADMIN_EMAIL", "CH_ADMIN2_EMAIL", "CH_BM_EMAIL",
              "CH_BOARD_EMAIL", "CH_ATTORNEY_EMAIL"):
        df[c] = df[c].apply(_valid_email)

    return df.reset_index(drop=True)


def _collapse_charter_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Rebuild rows split by a vertical cell merge, preserving second campuses.

    CSD merges Authorizer/Charter School/Acronym/PED for schools operating two
    campuses (ACES, AIMS). A CSV export writes the merged value on the first
    row of the span and leaves the rest blank, so the continuation row is a
    nameless orphan — but unlike the earlier grade-band splits, it now holds a
    genuinely different address and phone number.

    Taking first-non-empty per column would silently discard that second
    campus, so its address and phone are kept in CH_ADDRESS_ALT / CH_PHONE_ALT
    and the campus count in CH_CAMPUSES.
    """
    if df.empty:
        return df
    work = df.copy()
    # Group on PED where present (more reliable than a name CSD keeps editing),
    # falling back to the name for rows CSD has not numbered yet.
    key = work["CH_PED"].apply(_clean)
    key = key.where(key.ne(""), work["CH_NAME"].apply(_clean))
    work["_grp"] = key.replace("", None).ffill()
    work = work[work["_grp"].notna()]
    if work.empty:
        return df.iloc[0:0]

    def _first(s):
        return next((v for v in s if _clean(v)), "")

    agg = {c: _first for c in work.columns if c != "_grp"}
    out = work.groupby("_grp", sort=False).agg(agg)

    extras = work.groupby("_grp", sort=False).apply(
        lambda g: pd.Series({
            "CH_CAMPUSES": len(g),
            "CH_ADDRESS_ALT": " | ".join(
                dict.fromkeys(v for v in g["CH_ADDRESS"].map(_clean)
                              if v and v != _first(g["CH_ADDRESS"]))),
            "CH_PHONE_ALT": " | ".join(
                dict.fromkeys(v for v in g["CH_PHONE"].map(_clean)
                              if v and v != _first(g["CH_PHONE"]))),
        }), include_groups=False)

    return out.join(extras).reset_index(drop=True)


# ═════════════════════════════════════════════════════════════════════
# MATCHING
# ═════════════════════════════════════════════════════════════════════

def _build_roster_index(assign_c: pd.DataFrame):
    """Index the roster on both passes.

    Returns (light, heavy, squash_light, squash_heavy, display) where each of
    the first four maps a normalised key to a set of PED numbers, and display
    maps a key back to the roster names that produced it — so an ambiguous
    match can be reported with names a person recognises, not just codes.
    """
    light: dict[str, set[str]] = {}
    heavy: dict[str, set[str]] = {}
    sq_light: dict[str, set[str]] = {}
    sq_heavy: dict[str, set[str]] = {}
    display: dict[str, set[str]] = {}
    cols = ["PED_NO", "LEA_NAME", "LEA_NAME_LIGHT", "LEA_NAME_NORM"]
    for _, r in assign_c[cols].drop_duplicates().iterrows():
        ped, disp = r["PED_NO"], r["LEA_NAME"]
        if not ped:
            continue
        lt, hv = r["LEA_NAME_LIGHT"], r["LEA_NAME_NORM"]
        if lt:
            light.setdefault(lt, set()).add(ped)
            sq_light.setdefault(_squash(lt), set()).add(ped)
            display.setdefault(lt, set()).add(disp)
        for v in _name_variants(hv):
            heavy.setdefault(v, set()).add(ped)
            sq_heavy.setdefault(_squash(v), set()).add(ped)
            display.setdefault(v, set()).add(disp)
    return light, heavy, sq_light, sq_heavy, display


def _roster_collisions(assign_c: pd.DataFrame) -> pd.DataFrame:
    """Roster entries that the heavy pass collapses onto one key.

    These are the entries most likely to steal or block a match, so they are
    surfaced in the UI rather than left to be inferred from the match log.
    """
    if assign_c.empty:
        return pd.DataFrame(columns=["key", "schools"])
    g = (assign_c[assign_c["LEA_NAME_NORM"].ne("")]
         .groupby("LEA_NAME_NORM")
         .agg(schools=("LEA_NAME", lambda s: " | ".join(sorted(set(s)))),
              n=("LEA_NAME", "nunique"))
         .reset_index())
    out = g[g["n"] > 1].rename(columns={"LEA_NAME_NORM": "key"})
    return out[["key", "schools"]].sort_values("key")


def _match_source(src, raw_col, light_col, norm_col, idx,
                  override_map, token_cut, partial_cut, strict, source_label,
                  ped_col=None, roster_peds=None):
    """Match the charter tab against the assignments roster.

    Tier order is precision-first: PED → override → exact/light → squash/light
    → exact/heavy → squash/heavy → fuzzy/light → fuzzy/heavy.

    Since Aug 2026 CSD publishes a PED number, so the first tier resolves
    almost everything and the name tiers below are a fallback for rows CSD has
    not numbered. They are kept deliberately: CSD has restructured this
    workbook twice in two days, and a blank PED column should degrade to
    fuzzy matching rather than to zero matches.
    """
    light, heavy, sq_light, sq_heavy, display = idx
    roster_peds = roster_peds or set()
    light_choices = [n for n in light if n]
    heavy_choices = [n for n in heavy if n]

    def _describe(keys):
        names = set()
        for k in keys:
            names |= display.get(k, {k})
        return "; ".join(sorted(names)[:4])

    rows = []
    for i, r in src.reset_index(drop=True).iterrows():
        raw = _clean(r.get(raw_col, ""))
        lt  = _clean(r.get(light_col, ""))
        hv  = _clean(r.get(norm_col, ""))
        if not raw and not lt and not hv:
            continue

        chosen, method, score, reason = None, "", 0, ""

        # Tier 0 — the PED number CSD now publishes.
        src_ped = ped_canonical(_clean(r.get(ped_col, ""))) if ped_col else ""
        if src_ped and src_ped in roster_peds:
            chosen, method, score = src_ped, "ped", 100
        elif src_ped:
            reason = f"PED {src_ped} is not in the assignments roster"

        ped = (override_map.get(lt) or override_map.get(hv)
               or override_map.get(raw.lower()))
        if chosen:
            pass
        elif ped:
            chosen, method, score = ped, "override", 100
        else:
            for tier, key, table in (("exact/light", lt, light),
                                     ("squash/light", _squash(lt), sq_light),
                                     ("exact/heavy", hv, heavy),
                                     ("squash/heavy", _squash(hv), sq_heavy)):
                if chosen or not key:
                    continue
                if tier == "exact/heavy":
                    peds: set[str] = set()
                    for v in _name_variants(hv):
                        peds |= table.get(v, set())
                else:
                    peds = table.get(key, set())
                if len(peds) == 1:
                    chosen, method = next(iter(peds)), tier
                    score = 100 if tier.startswith("exact") else 99
                elif len(peds) > 1 and not reason:
                    reason = f"{tier} maps to several schools: {_describe([key])}"

            if not chosen and not strict and not reason:
                for label, probe, table, choices in (
                        ("light", lt, light, light_choices),
                        ("heavy", hv, heavy, heavy_choices)):
                    if chosen or not probe:
                        continue
                    tied, s, m = _best_fuzzy(probe, choices, token_cut, partial_cut)
                    if not tied:
                        continue
                    # Several roster spellings can tie legitimately when they
                    # are variants of ONE school, so judge by PED number, not
                    # by how many strings tied. A tie spanning two PED numbers
                    # is a real ambiguity and must not be guessed at.
                    peds = set()
                    for t in tied:
                        peds |= table.get(t, set())
                    if len(peds) == 1:
                        chosen, method, score = next(iter(peds)), f"fuzzy/{m}/{label}", s
                    else:
                        reason = (f"ambiguous — tied at {s:.0f} between "
                                  f"{_describe(tied)} ({', '.join(sorted(peds)[:4])})")
                if not chosen and not reason:
                    reason = ("below threshold" if len(hv) >= _MIN_FUZZY_LEN
                              else "name too short to fuzzy-match safely")
            elif not chosen and strict and not reason:
                reason = "no exact match (strict mode)"

        rows.append({
            "_src_idx": i,
            "source": source_label,
            "source_name": raw,
            "source_ped": src_ped,
            "source_name_light": lt,
            "source_name_norm": hv,
            "matched_PED_NO": chosen or "",
            "match_method": method,
            "match_score": round(float(score), 1),
            "reason": reason,
        })

    log = pd.DataFrame(rows, columns=[
        "_src_idx", "source", "source_name", "source_ped",
        "source_name_light", "source_name_norm", "matched_PED_NO",
        "match_method", "match_score", "reason"])

    # Fan-out guard: if two source rows resolved to the same PED_NO, keeping
    # both would duplicate that LEA in the merged frame. Keep the strongest
    # and tell the loser exactly which row and PED it lost to — without that,
    # a "duplicate" verdict is unactionable.
    hits = log[log["matched_PED_NO"].ne("")].copy()
    if not hits.empty:
        hits = hits.sort_values("match_score", ascending=False, kind="stable")
        dupe_mask = hits.duplicated("matched_PED_NO", keep="first")
        winners = (hits[~dupe_mask].set_index("matched_PED_NO")["source_name"].to_dict())
        for idx_, lr in hits[dupe_mask].iterrows():
            ped = lr["matched_PED_NO"]
            log.loc[idx_, "reason"] = (
                f"duplicate — {ped} was already claimed by "
                f"{winners.get(ped, '?')!r}")
            log.loc[idx_, "matched_PED_NO"] = ""
            log.loc[idx_, "match_method"] = ""
        hits = hits[~dupe_mask]

    return hits[["_src_idx", "matched_PED_NO", "match_method", "match_score"]], log


def merge_all(assign, districts, directory, overrides=None,
              token_cut=92, partial_cut=96, strict=False):
    """Return (merged_df, match_log_df).

    The assignments sheet is the spine. Districts join on PED_NO, and since
    CSD started publishing PED numbers, so do charters — name matching is now
    only the fallback path.
    """

    # ── Districts: straight PED_NO join ──────────────────────────────
    assign_d = assign[assign["LEA_TYPE"].apply(_is_district)].copy()
    merged_d = assign_d.merge(districts, on="PED_NO", how="left",
                              suffixes=("", "_DIST"))

    # ── Override lookup: charter name → PED_NO (raw + both passes) ───
    override_map: dict[str, str] = {}
    if overrides is not None and not overrides.empty:
        for _, orow in overrides.iterrows():
            cname = _clean(orow["CHARTER_NAME"])
            ped = _clean(orow["PED_NO"])
            if cname and ped:
                override_map[cname.lower()] = ped
                override_map[normalize_light(cname)] = ped
                override_map[normalize_name(cname)] = ped

    # ── Charters ─────────────────────────────────────────────────────
    assign_c = assign[assign["LEA_TYPE"].apply(_is_charter)].copy()
    roster_idx = _build_roster_index(assign_c)
    roster_peds = set(assign_c["PED_NO"].map(_clean)) - {""}

    merged_c = assign_c.copy()
    match_log = pd.DataFrame(columns=[
        "source", "source_name", "source_ped", "source_name_light",
        "source_name_norm", "matched_PED_NO", "match_method",
        "match_score", "reason"])

    if directory is not None and not directory.empty and "CH_NAME" in directory.columns:
        hits, match_log = _match_source(
            directory, "CH_NAME", "CH_NAME_LIGHT", "CH_NAME_NORM", roster_idx,
            override_map, token_cut, partial_cut, strict, "directory",
            ped_col="CH_PED", roster_peds=roster_peds)
        match_log = match_log.drop(columns=["_src_idx"])

        payload = directory.reset_index().rename(columns={"index": "_src_idx"})
        payload = hits.merge(payload, on="_src_idx", how="left").drop(columns=["_src_idx"])
        merged_c = merged_c.merge(
            payload, left_on="PED_NO", right_on="matched_PED_NO",
            how="left", suffixes=("", "_DIR"))
        merged_c = merged_c.drop(columns=["matched_PED_NO"], errors="ignore")
        merged_c["match_method"] = merged_c["match_method"].fillna("")
        merged_c["match_score"] = pd.to_numeric(
            merged_c["match_score"], errors="coerce").fillna(0.0)
    else:
        merged_c["match_method"] = ""
        merged_c["match_score"] = 0.0

    # ── Combine ──────────────────────────────────────────────────────
    merged = pd.concat([merged_d, merged_c], ignore_index=True)
    if "match_method" not in merged.columns:
        merged["match_method"] = ""
    merged["match_method"] = merged["match_method"].fillna("")

    dist_cols = ["DISTRICT NAME", "SUPT. E-MAIL", "BUS. MGR. E-MAIL"]
    has_dist = (
        merged[dist_cols].notna().any(axis=1)
        if all(c in merged.columns for c in dist_cols)
        else pd.Series(False, index=merged.index)
    )
    is_charter_row = merged["LEA_TYPE"].isin(["SC", "LC"])
    found = merged["match_method"].ne("") & is_charter_row

    def _filled(*cols):
        out = pd.Series(False, index=merged.index)
        for c in cols:
            if c in merged.columns:
                out |= merged[c].map(_clean).ne("")
        return out

    has_admin = _filled("CH_ADMIN_NAME", "CH_ADMIN_EMAIL")
    has_bm    = _filled("CH_BM_NAME", "CH_BM_EMAIL")

    merged["Matched"] = (merged["LEA_TYPE"].eq("D") & has_dist) | found
    # Partial no longer means "found in one tab but not the other" — there is
    # only one tab now. It means CSD has the school but left the charter rep
    # or the business manager blank, which is the same practical gap.
    merged["Partial"] = found & ~(has_admin & has_bm)
    merged["Matched_Directory"] = found
    return merged, match_log


# ═════════════════════════════════════════════════════════════════════
# DISPLAY HELPERS
# ═════════════════════════════════════════════════════════════════════

def _contact_block(label: str, fields: list[tuple[str, str]]):
    """Render a labelled contact section (uppercase label + gold hairline)."""
    st.markdown(f'<div class="section-label">{label}</div>', unsafe_allow_html=True)
    c1, c2 = st.columns(2)
    half = len(fields) // 2 + len(fields) % 2
    for idx, (k, v) in enumerate(fields):
        col = c1 if idx < half else c2
        with col:
            if "@" in v and v != "N/A":
                st.write(f"**{k}:** [{v}](mailto:{v})")
            else:
                st.write(f"**{k}:** {v}")


def _or_na(*vals):
    for v in vals:
        c = _clean(v)
        if c:
            return c
    return "N/A"


def display_contact(row: pd.Series):
    """Render a full contact card for one LEA."""
    lea_type = _clean(row.get("LEA_TYPE", "")).upper()
    charter = lea_type in {"SC", "LC"}
    matched = bool(row.get("Matched", False))
    partial = bool(row.get("Partial", False))
    type_label = "Charter" if charter else "District"

    if matched and partial:
        pill = '<span class="pill pill-partial">Partial</span>'
    elif matched:
        pill = '<span class="pill pill-matched">Matched</span>'
    else:
        pill = '<span class="pill pill-unmatched">Unmatched</span>'

    status = _clean(row.get("CH_STATUS", ""))
    status_badge = (f'<span class="badge badge-alert">{status}</span>'
                    if status else "")

    # Header: name + PED / type / status badges
    st.markdown(
        f'<div style="font-size:1.3rem;font-weight:600;color:#222;">'
        f'{_clean(row.get("LEA_NAME", "N/A"))}</div>'
        f'<div style="display:flex;gap:8px;align-items:center;margin:10px 0 4px;">'
        f'<span class="badge" style="font-family:monospace;">{_clean(row.get("PED_NO", "N/A"))}</span>'
        f'<span class="badge">{type_label}</span>{pill}{status_badge}</div>',
        unsafe_allow_html=True,
    )

    # Analyst
    _contact_block("Budget Analyst", [
        ("Name",       _or_na(row.get("Analyst"))),
        ("Email",      _or_na(row.get("Analyst Email"))),
        ("Supervisor", _or_na(row.get("Analyst Reports To"))),
        ("Phone",      _or_na(row.get("Analyst Phone"))),
    ])

    if charter:
        # Head administrator — the charter rep
        _contact_block("Charter Representative", [
            ("Name",  _or_na(row.get("CH_ADMIN_NAME"))),
            ("Email", _or_na(row.get("CH_ADMIN_EMAIL"))),
            ("Phone", _or_na(row.get("CH_PHONE"))),
            ("Authorizer", _or_na(row.get("CH_AUTHORIZER"))),
        ])

        # Some charters list a second head administrator; show it only then.
        if _clean(row.get("CH_ADMIN2_NAME")) or _clean(row.get("CH_ADMIN2_EMAIL")):
            _contact_block("Second Administrator", [
                ("Name",  _or_na(row.get("CH_ADMIN2_NAME"))),
                ("Email", _or_na(row.get("CH_ADMIN2_EMAIL"))),
            ])

        _contact_block("Business Manager", [
            ("Name",  _or_na(row.get("CH_BM_NAME"))),
            ("Email", _or_na(row.get("CH_BM_EMAIL"))),
            ("Phone", _or_na(row.get("CH_BM_PHONE"))),
        ])

        governance = [
            ("Board president", _or_na(row.get("CH_BOARD_NAME"))),
            ("Board email",     _or_na(row.get("CH_BOARD_EMAIL"))),
            ("Attorney",        _or_na(row.get("CH_ATTORNEY_NAME"))),
            ("Attorney email",  _or_na(row.get("CH_ATTORNEY_EMAIL"))),
        ]
        if any(v != "N/A" for _, v in governance):
            _contact_block("Governance", governance)

        profile = [
            ("Acronym",          _or_na(row.get("CH_ACRONYM"))),
            ("Contract term",    _or_na(row.get("CH_CONTRACT_TERM"))),
            ("Enrollment cap",   _or_na(row.get("CH_ENROLL_CAP"))),
            ("Grades authorized", _or_na(row.get("CH_GRADES_AUTH"))),
            ("Grades served",    _or_na(row.get("CH_GRADES_SERVED"))),
        ]
        if any(v != "N/A" for _, v in profile):
            _contact_block("Charter Profile", profile)

        gaps = []
        if not _clean(row.get("CH_ADMIN_NAME")) and not _clean(row.get("CH_ADMIN_EMAIL")):
            gaps.append("charter representative")
        if not _clean(row.get("CH_BM_NAME")) and not _clean(row.get("CH_BM_EMAIL")):
            gaps.append("business manager")
        if gaps and _clean(row.get("match_method")):
            st.caption("CSD's directory has this school but leaves the "
                       + " and ".join(gaps) + " blank.")
        elif not _clean(row.get("match_method")):
            st.caption("No row for this school in CSD's directory. Check the "
                       "match log, or add it to the overrides sheet.")

    else:
        # Superintendent
        supt_name = " ".join(filter(None, [
            _clean(row.get("SUPT. PREFIX", "")),
            _clean(row.get("SUPT. FIRST & M.I.", "")),
            _clean(row.get("SUPT. LAST NAME", "")),
        ])) or "N/A"
        _contact_block("Superintendent", [
            ("Name",  supt_name),
            ("Title", _or_na(row.get("SUPT. TITLE"))),
            ("Phone", _or_na(row.get("SUPT. PHONE"))),
            ("Email", _or_na(row.get("SUPT. E-MAIL"))),
        ])

        # Business Manager
        bm_name = " ".join(filter(None, [
            _clean(row.get("BUS. MGR. PREFIX", "")),
            _clean(row.get("BUS. MGR. FIRST & M. I.", "")),
            _clean(row.get("BUS. MGR. LAST NAME", "")),
        ])) or "N/A"
        _contact_block("Business Manager", [
            ("Name",  bm_name),
            ("Title", _or_na(row.get("BUS. MGR. TITLE"))),
            ("Phone", _or_na(row.get("BUS. MGR. PHONE"))),
            ("Email", _or_na(row.get("BUS. MGR. E-MAIL"))),
        ])

    # Address
    parts = []
    if charter:
        street = _clean(row.get("CH_ADDRESS", ""))
        city = state = zipcode = ""
        if street:
            parts.append(street)
        # Second campus, recovered from the merged continuation row.
        alt = _clean(row.get("CH_ADDRESS_ALT", ""))
        if alt:
            for a in alt.split(" | "):
                parts.append(f"Also: {a}")
        alt_phone = _clean(row.get("CH_PHONE_ALT", ""))
        if alt_phone:
            parts.append(f"Second campus phone: {alt_phone}")
    else:
        mail = _clean(row.get("MAILING ADDRESS", ""))
        city = _clean(row.get("CITY", ""))
        state = _clean(row.get("ST", "") or row.get("STATE", ""))
        zipcode = _clean(row.get("ZIP", ""))
        if mail:
            parts.append(mail)
    csz = ", ".join(filter(None, [city, state])) + f" {zipcode}".rstrip()
    if csz.strip():
        parts.append(csz.strip())
    if parts:
        st.markdown('<div class="section-label">Address</div>', unsafe_allow_html=True)
        st.write("  \n".join(parts))


def _collect_emails(df: pd.DataFrame) -> list[str]:
    """Extract all relevant emails from a contacts dataframe."""
    emails: set[str] = set()
    for _, r in df.iterrows():
        lea = _clean(r.get("LEA_TYPE", "")).upper()
        cols = (("CH_ADMIN_EMAIL", "CH_ADMIN2_EMAIL", "CH_BM_EMAIL")
                if lea in {"SC", "LC"}
                else ("SUPT. E-MAIL", "BUS. MGR. E-MAIL"))
        for col in cols:
            e = _valid_email(r.get(col, ""))
            if e:
                emails.add(e)
    return sorted(emails)


def _metric_card(label: str, value, alert: bool = False) -> str:
    """Return HTML for one summary metric card. Coral accent when alert."""
    if alert:
        border = "border:0.5px solid #ecc9c1;border-left:3px solid #c64c43;"
        lab_color, val_color = "#a8584c", "#c64c43"
    else:
        border = "border:0.5px solid #e3e3dd;"
        lab_color, val_color = "#8a8a82", "#245d62"
    return (
        f'<div style="{border}border-radius:8px;padding:13px 15px;">'
        f'<div style="font-size:11px;letter-spacing:.06em;text-transform:uppercase;'
        f'color:{lab_color};">{label}</div>'
        f'<div style="font-size:1.7rem;font-weight:600;color:{val_color};">{value}</div>'
        f'</div>'
    )


# ═════════════════════════════════════════════════════════════════════
# MAIN APP
# ═════════════════════════════════════════════════════════════════════

# Masthead (replaces st.title)
st.markdown(
    '<div class="ped-eyebrow">New Mexico PED · School Budget Bureau</div>'
    '<div class="ped-title">PED Contacts Manager</div>'
    '<div class="ped-rule"><span class="g"></span><span class="t"></span></div>',
    unsafe_allow_html=True,
)

# ── Sidebar: data loading & filters ─────────────────────────────────
st.sidebar.header("Data")

if st.sidebar.button("Refresh data"):
    st.cache_data.clear()
    st.rerun()

try:
    with st.spinner("Loading from Google Sheets…"):
        (raw_assign, raw_contacts, raw_dist, raw_directory,
         raw_overrides, load_notes, refresh_ts) = _fetch_sheets()
    st.sidebar.success(f"Loaded — {refresh_ts}")
    for note in load_notes:
        st.sidebar.warning(note)
    if len(raw_overrides) > 0:
        st.sidebar.caption(f"{len(raw_overrides)} charter override(s) active")
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# Prep & merge
assign    = _prep_assignments(raw_assign.copy(), raw_contacts.copy())
dists     = _prep_districts(raw_dist.copy())
directory = _prep_charter_directory(raw_directory.copy())

_ped_n = int(directory["CH_PED"].map(_clean).ne("").sum()) if len(directory) else 0
st.sidebar.caption(
    f"Charter directory: {len(directory)} schools · {_ped_n} with a PED number"
)

if len(raw_contacts):
    st.sidebar.caption(f"Analyst contacts: {len(raw_contacts)} analysts in the side table")
_unres = st.session_state.get("_assign_unresolved") or []
if _unres:
    st.sidebar.warning(
        "Analyst name(s) on the roster with no row in the Contacts side table "
        "(email/manager left blank): " + "; ".join(_unres)
    )

with st.sidebar.expander("Data sources", expanded=False):
    row_counts = {
        "assignments": len(assign),
        "districts": len(dists),
        "charter_directory": len(directory),
        "overrides": len(raw_overrides),
    }
    for label, key, owner in SOURCE_INFO:
        st.markdown(
            f"[{label}]({_edit_url(SHEETS[key])})  \n"
            f"<span style='font-size:11px;color:#8a8a82'>{owner} · "
            f"{row_counts.get(key, 0)} rows</span>",
            unsafe_allow_html=True,
        )
    st.caption(
        "CSD owns the charter workbook. If a tab is retired or re-gid'd, update "
        "the gid in SHEETS at the top of this file."
    )
    hdr_notes = st.session_state.get("_assign_header_notes") or []
    if hdr_notes:
        st.caption("Headers resolved despite drifted text: " + "; ".join(hdr_notes))
    side_cols = st.session_state.get("_assign_side_cols") or []
    if side_cols:
        st.caption("Filled from the Contacts side table: " + ", ".join(side_cols))

with st.sidebar.expander("Charter matching", expanded=False):
    token_cut  = st.slider("Token-set threshold", 80, 100, 92)
    partial_cut = st.slider("Partial threshold",   90, 100, 96)
    strict     = st.checkbox("Exact matches only")

merged, match_log = merge_all(assign, dists, directory, raw_overrides,
                               token_cut, partial_cut, strict)

# Roster entries the heavy pass collapses onto one key. Far less critical now
# that charters join on PED, but still the first thing to check if a fallback
# name match goes somewhere surprising.
_collisions = _roster_collisions(assign[assign["LEA_TYPE"].apply(_is_charter)])
if not _collisions.empty:
    with st.sidebar.expander(f"Roster collisions ({len(_collisions)})", expanded=False):
        st.caption("These roster names are indistinguishable to the heavy "
                   "normalisation pass. Only matters for fallback matching.")
        for _, cr in _collisions.iterrows():
            st.markdown(f"`{cr['key']}`  \n<span style='font-size:11px;color:#8a8a82'>"
                        f"{cr['schools']}</span>", unsafe_allow_html=True)

# Filters
st.sidebar.markdown("### Filters")

all_analysts = sorted(merged["Analyst"].dropna().unique().tolist())
all_supers   = sorted(merged["Analyst Reports To"].dropna().unique().tolist())

sel_analysts = st.sidebar.multiselect("Analyst", all_analysts, default=all_analysts)
sel_supers   = st.sidebar.multiselect("Supervisor", all_supers, default=all_supers)

lea_type_opt = st.sidebar.radio(
    "LEA type", ["All", "Districts only", "Charters only"], horizontal=True
)
only_unmatched = st.sidebar.checkbox("Only unmatched")
only_partial   = st.sidebar.checkbox("Only partial (missing rep or bus. mgr.)")

if st.sidebar.button("Reset filters"):
    for k in ("Analyst", "Supervisor"):
        st.session_state.pop(k, None)
    st.rerun()

# Apply filters
view = merged.copy()
if sel_analysts:
    view = view[view["Analyst"].isin(sel_analysts)]
if sel_supers:
    view = view[view["Analyst Reports To"].isin(sel_supers)]
if lea_type_opt == "Districts only":
    view = view[view["LEA_TYPE"].apply(_is_district)]
elif lea_type_opt == "Charters only":
    view = view[view["LEA_TYPE"].apply(_is_charter)]
if only_unmatched:
    view = view[~view["Matched"].astype(bool)]
if only_partial:
    view = view[view["Partial"].astype(bool)]

# ── Search ───────────────────────────────────────────────────────────
search = st.text_input("Search", placeholder="Name, PED #, email, city…")
if search:
    q = unidecode(search).lower()
    str_cols = view.select_dtypes(include="object").columns
    mask = pd.Series(False, index=view.index)
    for c in str_cols:
        mask |= view[c].astype(str).map(lambda x: unidecode(str(x)).lower()).str.contains(q, na=False)
    view = view[mask]

# ── Metrics row ──────────────────────────────────────────────────────
unmatched_n = int((~view["Matched"].astype(bool)).sum())
partial_n = int(view["Partial"].astype(bool).sum())
m1, m2, m3, m4, m5 = st.columns(5)
m1.markdown(_metric_card("Total LEAs", len(merged)), unsafe_allow_html=True)
m2.markdown(_metric_card("Showing", len(view)), unsafe_allow_html=True)
m3.markdown(_metric_card("Matched", int(view["Matched"].astype(bool).sum())), unsafe_allow_html=True)
m4.markdown(_metric_card("Partial", partial_n, alert=partial_n > 0), unsafe_allow_html=True)
m5.markdown(_metric_card("Unmatched", unmatched_n, alert=unmatched_n > 0),
            unsafe_allow_html=True)
st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════
# PAGINATED CONTACT BROWSER
# ═════════════════════════════════════════════════════════════════════

PAGE_SIZE = 10

if len(view) == 0:
    st.info("No LEAs match these filters. Clear the search box or reset the filters to start over.")
else:
    # Build a summary table for browsing
    summary_cols = ["PED_NO", "LEA_NAME", "LEA_TYPE", "Analyst", "Matched", "Partial"]
    summary = view[[c for c in summary_cols if c in view.columns]].copy()
    summary.index = view.index  # preserve index for lookup

    total_pages = max(1, -(-len(summary) // PAGE_SIZE))  # ceil div

    # Page state
    if "page" not in st.session_state:
        st.session_state.page = 0
    page = st.session_state.page
    page = min(page, total_pages - 1)

    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(summary))
    page_df = summary.iloc[start:end]

    st.markdown(f"#### Contacts ({start+1}–{end} of {len(summary)})")

    # Render clickable rows. Streamlit expander labels accept markdown
    # color syntax (:green[]/:red[]) but not custom hex, so the list uses
    # green/orange/red while the expanded card uses exact brand pills.
    for real_idx, srow in page_df.iterrows():
        matched = bool(srow.get("Matched", False))
        is_partial = bool(srow.get("Partial", False))
        if matched and is_partial:
            status = ":orange[Partial]"
        elif matched:
            status = ":green[Matched]"
        else:
            status = ":red[Unmatched]"
        lea_type_label = "Charter" if _clean(srow.get("LEA_TYPE","")).upper() in {"SC","LC"} else "District"
        label = (
            f"{status}  **{_clean(srow.get('PED_NO',''))}** — "
            f"{_clean(srow.get('LEA_NAME',''))}  ·  "
            f"{lea_type_label} · {_clean(srow.get('Analyst',''))}"
        )
        with st.expander(label, expanded=False):
            display_contact(view.loc[real_idx])

    # Pagination controls
    pcol1, pcol2, pcol3, pcol4, pcol5 = st.columns([1, 1, 2, 1, 1])
    with pcol1:
        if st.button("First", disabled=(page == 0), key="pg_first"):
            st.session_state.page = 0
            st.rerun()
    with pcol2:
        if st.button("‹ Prev", disabled=(page == 0), key="pg_prev"):
            st.session_state.page = page - 1
            st.rerun()
    with pcol3:
        st.markdown(
            f"<div style='text-align:center;padding-top:6px;color:#8a8a82'>"
            f"Page {page+1} of {total_pages}</div>",
            unsafe_allow_html=True,
        )
    with pcol4:
        if st.button("Next ›", disabled=(page >= total_pages - 1), key="pg_next"):
            st.session_state.page = page + 1
            st.rerun()
    with pcol5:
        if st.button("Last", disabled=(page >= total_pages - 1), key="pg_last"):
            st.session_state.page = total_pages - 1
            st.rerun()


# ═════════════════════════════════════════════════════════════════════
# BATCH EMAIL
# ═════════════════════════════════════════════════════════════════════

with st.expander("Batch email", expanded=False):
    email_mode = st.radio(
        "Recipients:",
        ["Current filtered view", "Specific LEAs", "By analyst"],
        horizontal=True,
    )
    target = view.copy()
    if email_mode == "Specific LEAs":
        leas = st.multiselect("Choose LEAs:", sorted(view["LEA_NAME"].unique()))
        if leas:
            target = view[view["LEA_NAME"].isin(leas)]
    elif email_mode == "By analyst":
        a = st.selectbox("Choose analyst:", sorted(view["Analyst"].dropna().unique()))
        if a:
            target = view[view["Analyst"] == a]

    subject = st.text_input("Subject:", key="email_subject")

    emails = _collect_emails(target)
    st.caption(f"{len(emails)} email addresses")

    if emails:
        # Always show the copy-paste box (most reliable)
        st.code("; ".join(emails), language=None)

        # Mailto link (works for small lists)
        mailto = f"mailto:{';'.join(emails)}"
        if subject:
            mailto += f"?subject={urllib.parse.quote(subject)}"
        st.markdown(f"[Open in email client]({mailto})")
    else:
        st.warning("No email addresses in this selection. Widen the filters, or check the match log for schools with no row in CSD's directory.")


# ═════════════════════════════════════════════════════════════════════
# DATA TABLE & DOWNLOADS
# ═════════════════════════════════════════════════════════════════════

with st.expander("Data table", expanded=False):
    show_cols = [c for c in [
        "PED_NO", "LEA_NAME", "LEA_TYPE", "Analyst",
        "Analyst Reports To", "Matched", "Partial",
        "CH_ADMIN_NAME", "CH_ADMIN_EMAIL", "CH_BM_NAME", "CH_BM_EMAIL",
        "CH_ACRONYM", "CH_STATUS", "match_method",
    ] if c in view.columns]
    st.dataframe(view[show_cols], use_container_width=True, height=400)

with st.expander("Charter match log", expanded=False):
    if match_log.empty:
        st.info("No charter rows were processed. Check that the charter tab loaded.")
    else:
        unresolved = match_log[match_log["matched_PED_NO"].eq("")]
        by_ped = int(match_log["match_method"].eq("ped").sum())
        st.caption(
            f"{len(match_log)} charter source rows · "
            f"{len(match_log) - len(unresolved)} matched "
            f"({by_ped} on PED number, {len(match_log) - len(unresolved) - by_ped} "
            f"by name) · {len(unresolved)} unresolved"
        )
        st.dataframe(match_log, use_container_width=True, height=340)

st.subheader("Downloads")
dl1, dl2, dl3 = st.columns(3)
with dl1:
    st.download_button("Filtered", view.to_csv(index=False).encode("utf-8-sig"),
                       "filtered_contacts.csv", "text/csv")
with dl2:
    st.download_button("All contacts", merged.to_csv(index=False).encode("utf-8-sig"),
                       "all_contacts.csv", "text/csv")
with dl3:
    st.download_button("Match log", match_log.to_csv(index=False).encode("utf-8-sig"),
                       "match_log.csv", "text/csv")

# Footer
st.markdown("---")
st.caption("New Mexico Public Education Department · School Budget Bureau")