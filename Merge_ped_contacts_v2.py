# Merge_ped_contacts_v2.py — PED Contacts Manager (Streamlined)
# Merges analyst assignments with district/charter contact sheets.
# Charter matching uses fuzzy name matching (no PED# in charter sheet).
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
.badge {
    font-size: 12px; color: #5f5e5a; background: #f1efe8;
    padding: 3px 9px; border-radius: 6px;
}

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


# ── Name normalisation (for charter fuzzy matching) ──────────────────
_ARTICLES = re.compile(r"\b(the|a|an)\b")
_SUFFIXES = re.compile(
    r"\b(public|charter|school|schools|academy|district|high|middle|"
    r"elementary|prep|preparatory|learning|center|centers)\b"
)
_KNOWN_RENAMES = {
    "academy for technology and the classics the":
        "academy for technology and the classics",
    "albuquerque aviation academy formerly known as sams":
        "albuquerque aviation academy",
    "northpoint charter school formerly southwest secondary learning center":
        "northpoint charter school",
}

def normalize_name(name: str) -> str:
    if pd.isna(name) or str(name).strip() == "":
        return ""
    s = unidecode(str(name)).lower()
    s = re.sub(r"\(formerly[^)]*\)", " ", s)
    s = re.sub(r"[-/.,&]+", " ", s)
    s = _ARTICLES.sub(" ", s)
    s = _SUFFIXES.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    return _KNOWN_RENAMES.get(s, s)

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


def _best_fuzzy(target: str, choices: list[str],
                token_cut=92, partial_cut=96):
    """Return (matched_name, score, method) or (None, 0, '')."""
    if not target or not choices:
        return None, 0, ""
    b1 = process.extractOne(target, choices, scorer=fuzz.token_set_ratio)
    if b1 and b1[1] >= token_cut:
        return b1[0], b1[1], "token_set"
    b2 = process.extractOne(target, choices, scorer=fuzz.partial_ratio)
    if b2 and b2[1] >= partial_cut:
        return b2[0], b2[1], "partial"
    return None, 0, ""


# ═════════════════════════════════════════════════════════════════════
# DATA LOADING
# ═════════════════════════════════════════════════════════════════════

SHEETS = {
    "districts":   "https://docs.google.com/spreadsheets/d/1vkzVbwmg3LktPWlxK-SIi28hSIaP2YIG_wnp2FgWPYE/export?format=csv&gid=0",
    "charters":    "https://docs.google.com/spreadsheets/d/1GQvRVXTwje6mhCyeZsGxUMH64RzAPOcstT24ANxcGK8/export?format=csv&gid=0",
    "assignments": "https://docs.google.com/spreadsheets/d/1uZY1Ep9jMpachr7MtBBy5Rwi25iVv80jp0X3i_e1ezg/export?format=csv&gid=1629654616",
    "overrides":   "https://docs.google.com/spreadsheets/d/1K-Hh7p9I30wjjumTeKy44D_VZnGmL87oJuNEYLywt5w/export?format=csv&gid=0",
}

@st.cache_data(ttl=3600, show_spinner=False)
def _fetch_sheets():
    """Load all four sheets; return (assign, districts, charters, overrides, timestamp)."""
    ts = datetime.now().strftime("%b %d %Y, %I:%M %p")
    assign    = pd.read_csv(SHEETS["assignments"], dtype=str).fillna("")
    districts = pd.read_csv(SHEETS["districts"],   dtype=str).fillna("")
    charters  = pd.read_csv(SHEETS["charters"],    dtype=str).fillna("")
    try:
        overrides = pd.read_csv(SHEETS["overrides"], dtype=str).fillna("")
        # Normalise column names
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
    return assign, districts, charters, overrides, ts


# ── Column normalisation helpers ─────────────────────────────────────

def _find_col(df: pd.DataFrame, candidates: list[str]):
    """Return first matching column name (case-insensitive) or None."""
    lc = {c.strip().lower(): c for c in df.columns}
    for cand in candidates:
        if cand.strip().lower() in lc:
            return lc[cand.strip().lower()]
    return None


def _prep_assignments(df: pd.DataFrame) -> pd.DataFrame:
    renames = {
        "PED_NO":     ["PED NO", "ped no", "PED_NO", "Ped No"],
        "LEA_TYPE":   ["DISTRICT, STATE, OR LOCAL CHARTER", "LEA TYPE", "LEA", "TYPE"],
        "LEA_NAME":   ["DISTRICT/CHARTER NAME", "LEA NAME", "NAME"],
        # Same fuzzy-header treatment for the analyst fields, since these
        # were previously assumed to match verbatim and broke the app the
        # moment the source sheet's header text shifted at all.
        "Analyst":            ["Analyst", "Budget Analyst", "ANALYST"],
        "Analyst Email":      ["Analyst Email", "Analyst E-mail", "ANALYST EMAIL"],
        # CONFIRMED via screenshot of the live sheet (Jul 2026): the header
        # was renamed from "Analyst Reports To" -> "Analyst Manager", with
        # two new sibling columns added alongside it (captured below but
        # not yet surfaced in the UI — see note near display_contact()).
        "Analyst Reports To": ["Analyst Reports To", "Analyst Manager", "Reports To",
                                "Supervisor", "Analyst Supervisor", "ANALYST REPORTS TO",
                                "ANALYST MANAGER"],
        "Analyst Phone":      ["Analyst Phone", "ANALYST PHONE"],
        "Analyst Manager Email": ["Analyst Manager Email", "ANALYST MANAGER EMAIL"],
        "Analyst Manager Phone": ["Analyst Manager Phone", "ANALYST MANAGER PHONE"],
    }
    for canon, cands in renames.items():
        col = _find_col(df, cands)
        if col:
            df = df.rename(columns={col: canon})

    # Fail loudly and usefully instead of a bare KeyError three screens away.
    required = ["PED_NO", "LEA_TYPE", "LEA_NAME"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise KeyError(
            f"_prep_assignments: could not find required column(s) {missing} "
            f"in the assignments sheet. Raw columns present: {df.columns.tolist()}"
        )

    # Analyst fields are used downstream (filters, contact cards) but aren't
    # strictly required to build PED_NO/LEA_TYPE/LEA_NAME — so instead of
    # crashing, backfill with empty strings and surface a warning in the app
    # if the source sheet drops/renames one of them again in the future.
    for optional_col in ["Analyst", "Analyst Email", "Analyst Reports To",
                          "Analyst Phone", "Analyst Manager Email", "Analyst Manager Phone"]:
        if optional_col not in df.columns:
            st.sidebar.warning(
                f"⚠️ Column '{optional_col}' not found in assignments sheet — "
                f"filters/cards using it will show blanks. "
                f"Raw columns: {df.columns.tolist()}"
            )
            df[optional_col] = ""

    df["PED_NO"]        = df["PED_NO"].apply(ped_canonical)
    df["LEA_TYPE"]      = df["LEA_TYPE"].str.strip().str.upper()
    df["LEA_NAME_NORM"] = df["LEA_NAME"].apply(normalize_name)
    return df


def _prep_districts(df: pd.DataFrame) -> pd.DataFrame:
    col = _find_col(df, ["ped no", "district no.", "PED NO"])
    if col:
        df = df.rename(columns={col: "PED NO"})
    df["PED_NO"] = df["PED NO"].apply(ped_canonical)
    return df


def _prep_charters(df: pd.DataFrame) -> pd.DataFrame:
    ct = _find_col(df, ["Charter Type", "CHARTER TYPE", "LEA Type", "Type"])
    cn = _find_col(df, ["CHARTER NAME", "School Name", "Organization Name", "Name"])
    if ct and cn:
        df["CHARTER TYPE"]      = df[ct].str.strip().str.title()
        df["CHARTER NAME"]      = df[cn].str.strip()
        df["CHARTER_NAME_NORM"] = df["CHARTER NAME"].apply(normalize_name)
    return df


# ═════════════════════════════════════════════════════════════════════
# MERGE
# ═════════════════════════════════════════════════════════════════════

def merge_all(assign, districts, charters, overrides=None,
              token_cut=92, partial_cut=96, strict=False):
    """Return (merged_df, match_log_df).

    Overrides are applied first: charter names in the overrides table
    are matched by exact CHARTER_NAME → PED_NO, skipping fuzzy logic.
    Remaining charters fall through to fuzzy matching as before.
    """

    # ── Districts: straight PED_NO join ──────────────────────────────
    assign_d = assign[assign["LEA_TYPE"].apply(_is_district)].copy()
    merged_d = assign_d.merge(districts, on="PED_NO", how="left",
                              suffixes=("", "_DIST"))

    # ── Build override lookup: charter name (as-is) → PED_NO ────────
    override_map: dict[str, str] = {}
    if overrides is not None and not overrides.empty:
        for _, orow in overrides.iterrows():
            cname = _clean(orow["CHARTER_NAME"])
            ped = _clean(orow["PED_NO"])
            if cname and ped:
                override_map[cname.lower()] = ped

    # ── Charters: overrides first, then fuzzy ────────────────────────
    assign_c = assign[assign["LEA_TYPE"].apply(_is_charter)].copy()

    # Build lookup: normalised name → set of PED_NOs
    name_to_ped: dict[str, set[str]] = {}
    for _, r in assign_c[["PED_NO", "LEA_NAME_NORM"]].drop_duplicates().iterrows():
        for v in _name_variants(r["LEA_NAME_NORM"]):
            name_to_ped.setdefault(v, set()).add(r["PED_NO"])

    unique_norms = list({n for ns in name_to_ped for n in [ns] if n})

    matches = []
    for i, r in charters.reset_index(drop=True).iterrows():
        raw_name = _clean(r.get("CHARTER NAME", ""))
        norm = r.get("CHARTER_NAME_NORM", "")
        if not raw_name and not norm:
            continue

        chosen, method, score = None, "", 0

        # 1) Check overrides first (exact charter name match)
        override_ped = override_map.get(raw_name.lower())
        if override_ped:
            chosen, method, score = override_ped, "override", 100
        else:
            # 2) Try exact / variant match
            cand_peds: set[str] = set()
            for v in _name_variants(norm):
                cand_peds |= name_to_ped.get(v, set())

            if len(cand_peds) == 1:
                chosen, method, score = next(iter(cand_peds)), "exact", 100
            elif not strict:
                # 3) Fuzzy fallback
                best_name, s, m = _best_fuzzy(
                    norm, unique_norms, token_cut, partial_cut
                )
                if best_name:
                    ps = name_to_ped.get(best_name, set())
                    if len(ps) == 1:
                        chosen, method, score = next(iter(ps)), f"fuzzy/{m}", s

        if chosen:
            matches.append({
                "_charter_idx": i,
                "matched_PED_NO": chosen,
                "match_method": method,
                "match_score": score,
            })

    match_log = pd.DataFrame(matches)
    merged_c = assign_c.copy()

    if not match_log.empty:
        # Join charter contact rows via PED_NO
        charter_contacts = charters.reset_index().rename(
            columns={"index": "_charter_idx"}
        )
        matched = match_log.merge(charter_contacts, on="_charter_idx", how="left")
        merged_c = merged_c.merge(
            matched, left_on="PED_NO", right_on="matched_PED_NO",
            how="left", suffixes=("", "_CHARTER"),
        )
    for col in ("match_method", "match_score"):
        if col not in merged_c.columns:
            merged_c[col] = ""

    # ── Combine ──────────────────────────────────────────────────────
    merged = pd.concat([merged_d, merged_c], ignore_index=True)

    # Matched flag
    dist_cols = ["DISTRICT NAME", "SUPT. E-MAIL", "BUS. MGR. E-MAIL"]
    has_dist = (
        merged[dist_cols].notna().any(axis=1)
        if all(c in merged.columns for c in dist_cols)
        else pd.Series(False, index=merged.index)
    )
    has_charter = merged.get("match_method", pd.Series(dtype=str)).fillna("").ne("")
    merged["Matched"] = (
        (merged["LEA_TYPE"].eq("D") & has_dist)
        | (merged["LEA_TYPE"].isin(["SC", "LC"]) & has_charter)
    )
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


def display_contact(row: pd.Series):
    """Render a full contact card for one LEA."""
    lea_type = _clean(row.get("LEA_TYPE", "")).upper()
    charter = lea_type in {"SC", "LC"}
    matched = bool(row.get("Matched", False))
    type_label = "Charter" if charter else "District"

    pill = ('<span class="pill pill-matched">Matched</span>' if matched
            else '<span class="pill pill-unmatched">Unmatched</span>')

    # Header: name + PED / type / status badges
    st.markdown(
        f'<div style="font-size:1.3rem;font-weight:600;color:#222;">'
        f'{_clean(row.get("LEA_NAME", "N/A"))}</div>'
        f'<div style="display:flex;gap:8px;align-items:center;margin:10px 0 4px;">'
        f'<span class="badge" style="font-family:monospace;">{_clean(row.get("PED_NO", "N/A"))}</span>'
        f'<span class="badge">{type_label}</span>{pill}</div>',
        unsafe_allow_html=True,
    )

    # Analyst
    # NOTE: "Analyst Manager Email" / "Analyst Manager Phone" are now
    # available from the sheet (added alongside the "Analyst Manager"
    # rename) but aren't shown here yet — say the word and I'll add them
    # as extra fields in this block.
    _contact_block("Budget Analyst", [
        ("Name",       _clean(row.get("Analyst", "N/A"))),
        ("Email",      _clean(row.get("Analyst Email", "N/A"))),
        ("Supervisor", _clean(row.get("Analyst Reports To", "N/A"))),
        ("Phone",      _clean(row.get("Analyst Phone", "N/A"))),
    ])

    if charter:
        # Rep
        rep_name = " ".join(filter(None, [
            _clean(row.get("CHARTER REP PREFIX", "")),
            _clean(row.get("CHARTER REP FIRST NAME", "")),
            _clean(row.get("CHARTER REP LAST NAME", "")),
        ])) or "N/A"
        _contact_block("Charter Representative", [
            ("Name",  rep_name),
            ("Title", _clean(row.get("CHARTER REP TITLE", "N/A"))),
            ("Phone", _clean(row.get("REPRESENTATIVE CONTACT PHONE #", ""))
                      or _clean(row.get("PHONE", "N/A"))),
            ("Email", _clean(row.get("REPRESENTATIVE EMAIL", "N/A"))),
        ])

        # Fiscal
        fisc_name = " ".join(filter(None, [
            _clean(row.get("FISCAL CONTACT PREFIX", "")),
            _clean(row.get("FISCAL CONTACT FIRST NAME", "")),
            _clean(row.get("FISCAL CONTACT LAST NAME", "")),
        ])) or "N/A"
        _contact_block("Fiscal Contact", [
            ("Name",  fisc_name),
            ("Title", _clean(row.get("CONTACT TITLE", "N/A"))),
            ("Phone", _clean(row.get("FISCAL CONTACT PHONE #", "N/A"))),
            ("Email", _clean(row.get("CONTACT EMAIL", "N/A"))),
        ])
    else:
        # Superintendent
        supt_name = " ".join(filter(None, [
            _clean(row.get("SUPT. PREFIX", "")),
            _clean(row.get("SUPT. FIRST & M.I.", "")),
            _clean(row.get("SUPT. LAST NAME", "")),
        ])) or "N/A"
        _contact_block("Superintendent", [
            ("Name",  supt_name),
            ("Title", _clean(row.get("SUPT. TITLE", "N/A"))),
            ("Phone", _clean(row.get("SUPT. PHONE", "N/A"))),
            ("Email", _clean(row.get("SUPT. E-MAIL", "N/A"))),
        ])

        # Business Manager
        bm_name = " ".join(filter(None, [
            _clean(row.get("BUS. MGR. PREFIX", "")),
            _clean(row.get("BUS. MGR. FIRST & M. I.", "")),
            _clean(row.get("BUS. MGR. LAST NAME", "")),
        ])) or "N/A"
        _contact_block("Business Manager", [
            ("Name",  bm_name),
            ("Title", _clean(row.get("BUS. MGR. TITLE", "N/A"))),
            ("Phone", _clean(row.get("BUS. MGR. PHONE", "N/A"))),
            ("Email", _clean(row.get("BUS. MGR. E-MAIL", "N/A"))),
        ])

    # Address
    parts = []
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
        if lea in {"SC", "LC"}:
            for col in ("REPRESENTATIVE EMAIL", "CONTACT EMAIL"):
                e = _clean(r.get(col, ""))
                if e:
                    emails.add(e)
        else:
            for col in ("SUPT. E-MAIL", "BUS. MGR. E-MAIL"):
                e = _clean(r.get(col, ""))
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
        raw_assign, raw_dist, raw_chart, raw_overrides, refresh_ts = _fetch_sheets()
    st.sidebar.success(f"Loaded — {refresh_ts}")
    if len(raw_overrides) > 0:
        st.sidebar.caption(f"{len(raw_overrides)} charter override(s) active")
except Exception as e:
    st.error(f"Failed to load data: {e}")
    st.stop()

# Prep & merge
assign  = _prep_assignments(raw_assign.copy())
dists   = _prep_districts(raw_dist.copy())
charts  = _prep_charters(raw_chart.copy())

with st.sidebar.expander("Charter matching", expanded=False):
    token_cut  = st.slider("Token-set threshold", 80, 100, 92)
    partial_cut = st.slider("Partial threshold",   90, 100, 96)
    strict     = st.checkbox("Exact matches only")

merged, match_log = merge_all(assign, dists, charts, raw_overrides,
                              token_cut, partial_cut, strict)

# Filters
st.sidebar.markdown("### Filters")

all_analysts = sorted(merged["Analyst"].dropna().unique().tolist())
all_supers   = sorted(merged["Analyst Reports To"].dropna().unique().tolist())

sel_analysts = st.sidebar.multiselect("Analyst", all_analysts, default=all_analysts)
sel_supers   = st.sidebar.multiselect("Supervisor", all_supers, default=all_supers)

lea_type_opt = st.sidebar.radio(
    "LEA type", ["All", "Districts only", "Charters only"], horizontal=True
)

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
unmatched_n = int((~view["Matched"]).sum())
m1, m2, m3, m4 = st.columns(4)
m1.markdown(_metric_card("Total LEAs", len(merged)), unsafe_allow_html=True)
m2.markdown(_metric_card("Showing", len(view)), unsafe_allow_html=True)
m3.markdown(_metric_card("Matched", int(view["Matched"].sum())), unsafe_allow_html=True)
m4.markdown(_metric_card("Unmatched", unmatched_n, alert=unmatched_n > 0),
            unsafe_allow_html=True)
st.markdown("<div style='height:1.2rem'></div>", unsafe_allow_html=True)

# ═════════════════════════════════════════════════════════════════════
# PAGINATED CONTACT BROWSER
# ═════════════════════════════════════════════════════════════════════

PAGE_SIZE = 10

if len(view) == 0:
    st.info("No results match your filters.")
else:
    # Build a summary table for browsing
    summary_cols = ["PED_NO", "LEA_NAME", "LEA_TYPE", "Analyst", "Matched"]
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
    # green/red while the expanded card uses exact brand teal/coral pills.
    for real_idx, srow in page_df.iterrows():
        matched = bool(srow.get("Matched", False))
        status = ":green[Matched]" if matched else ":red[Unmatched]"
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
        st.warning("No email addresses found in selection.")


# ═════════════════════════════════════════════════════════════════════
# DATA TABLE & DOWNLOADS
# ═════════════════════════════════════════════════════════════════════

with st.expander("Data table", expanded=False):
    show_cols = [c for c in [
        "PED_NO", "LEA_NAME", "LEA_TYPE", "Analyst",
        "Analyst Reports To", "Matched", "match_method", "match_score"
    ] if c in view.columns]
    st.dataframe(view[show_cols], use_container_width=True, height=400)

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