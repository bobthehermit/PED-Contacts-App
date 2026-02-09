# merge_ped_contacts_app_improved.py
import re
import pandas as pd
import streamlit as st
from unidecode import unidecode
from rapidfuzz import process, fuzz
import urllib.parse
from pathlib import Path
from io import BytesIO
from PIL import Image
import os
import base64

st.set_page_config(
    page_title="PED Contacts Manager", 
    layout="wide",
    initial_sidebar_state="expanded"
)

# =========================
# Styling & Branding
# =========================

# Custom CSS - PED Brand Style Guide compliant
st.markdown("""
    <style>
        /* PED Brand Colors:
           Primary: #245d62 (teal-blue)
           Secondary: #c64c43 (coral-red), #f4784e (orange), #edc872 (gold), #fef0c3 (light yellow)
        */
        
        .block-container { 
            padding-top: 1.0rem !important; 
        }
        
        /* Metrics - using PED primary color */
        .stMetric { 
            background-color: #fef0c3; 
            padding: 15px; 
            border-radius: 5px; 
            border-left: 4px solid #245d62;
        }
        .stMetric label { 
            color: #245d62 !important; 
            font-weight: 600; 
            font-size: 0.9rem;
        }
        .stMetric [data-testid="stMetricValue"] { 
            color: #245d62 !important; 
            font-weight: 700;
            font-size: 1.8rem;
        }
        .stMetric [data-testid="stMetricDelta"] { 
            color: #245d62 !important; 
        }
        
        /* Contact cards - light yellow background with teal accent */
        .contact-card { 
            background-color: #fef0c3; 
            padding: 20px; 
            border-radius: 8px; 
            margin: 10px 0;
            border-left: 5px solid #245d62;
            color: #333333;
        }
        
        .contact-section { 
            margin: 15px 0;
            padding: 12px 0;
            border-bottom: 1px solid #edc872;
        }
        .contact-section:last-child { 
            border-bottom: none; 
        }
        
        /* Download Buttons - white text on teal */
        .stDownloadButton button {
            width: 100%;
            background-color: #245d62;
            color: white !important;
        }
        .stDownloadButton button p,
        .stDownloadButton button span {
            color: white !important;
        }
        .stDownloadButton button:hover {
            background-color: #1a474b;
            border-color: #1a474b;
            color: white !important;
        }
        .stDownloadButton button:hover p,
        .stDownloadButton button:hover span {
            color: white !important;
        }
        
        /* Headers - PED primary teal */
        h1 { 
            color: #245d62; 
            font-weight: 700;
        }
        h2 { 
            color: #245d62; 
            font-weight: 600;
        }
        h3 { 
            color: #245d62; 
            font-weight: 600;
        }
        
        /* Subheaders */
        .contact-section h4,
        .contact-section strong {
            color: #245d62;
        }
        
        /* Batch email section - light yellow */
        .batch-email-section {
            background-color: #fef0c3;
            padding: 20px;
            border-radius: 8px;
            margin: 10px 0;
            border: 2px solid #edc872;
            color: #333333;
        }
        
        /* Sidebar styling */
        [data-testid="stSidebar"] {
            background-color: #f5f5f5;
        }
        
        /* Filter tags - white text on dark background */
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] {
            background-color: #245d62 !important;
            color: white !important;
        }
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] span {
            color: white !important;
        }
        /* X button on filter tags */
        [data-testid="stSidebar"] .stMultiSelect [data-baseweb="tag"] svg {
            fill: white !important;
        }
        
        /* Links - use secondary coral color for accent */
        a {
            color: #c64c43;
            text-decoration: none;
        }
        a:hover {
            color: #a03d35;
            text-decoration: underline;
        }
        
        /* Ensure all text is visible with good contrast */
        p, label, span, div { 
            color: #333333; 
        }
        
        /* Info/success/warning boxes */
        .stAlert {
            border-left: 4px solid #245d62;
        }
    </style>
""", unsafe_allow_html=True)

# Logo setup
LOGO_PATH = Path(__file__).parent / "300 DPI NM PED Logo JPEG.jpg"
LOGO_LINK = "https://web.ped.nm.gov/bureaus/school-budget-bureau/"
MAX_LOGO_HEIGHT = 90

def load_logo():
    if LOGO_PATH.exists():
        try:
            img = Image.open(LOGO_PATH)
            buf = BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            return f'<a href="{LOGO_LINK}" target="_blank"><img src="data:image/png;base64,{b64}" style="max-height:{MAX_LOGO_HEIGHT}px; height:auto; max-width:100%;"></a>'
        except:
            return None
    return None

logo_html = load_logo()
if logo_html:
    st.sidebar.markdown(logo_html, unsafe_allow_html=True)
st.sidebar.caption("School Budget Bureau")

# =========================
# Helper Functions
# =========================

def ped_no_canonical(x: str) -> str:
    """Normalize PED numbers to XXX-XXX format"""
    if pd.isna(x) or str(x).strip() == "":
        return ""
    s = str(x).strip()
    if "-" in s:
        left, right = s.split("-", 1)
        try:
            return f"{int(left):03d}-{int(right):03d}"
        except:
            return s
    try:
        return f"{int(s):03d}-000"
    except:
        return s

_ARTICLE_RX = re.compile(r"\b(the|a|an)\b")
_COMMON_SUFFIXES_RX = re.compile(
    r"\b(public|charter|school|schools|academy|district|high|middle|elementary|prep|preparatory|learning|center|centers)\b"
)

KNOWN_RENAMES = {
    "academy for technology and the classics the": "academy for technology and the classics",
    "albuquerque aviation academy formerly known as sams": "albuquerque aviation academy",
    "northpoint charter school formerly southwest secondary learning center": "northpoint charter school",
}

def normalize_name(name: str) -> str:
    """Normalize school names for matching"""
    if pd.isna(name) or str(name).strip() == "":
        return ""
    s = unidecode(str(name)).lower()
    s = re.sub(r"\(formerly[^)]*\)", " formerly ", s)
    s = re.sub(r"[-/.,&]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    s = _ARTICLE_RX.sub(" ", s)
    s = _COMMON_SUFFIXES_RX.sub(" ", s)
    s = re.sub(r"\s+", " ", s).strip()
    key = s.replace("  ", " ").strip()
    return KNOWN_RENAMES.get(key, key)

def smart_exact_variants(n: str) -> set:
    """Generate name variants for matching"""
    if not n:
        return {""}
    v = {n}
    v.add(n.replace(" the ", " ").strip())
    v.add(n.replace(" academy", "").strip())
    v.add(n.replace(" academies", "").strip())
    v.add(n.replace(" high", "").strip())
    v.add(n.replace(" prep", " preparatory"))
    return {re.sub(r"\s+", " ", x).strip() for x in v if x}

def is_charter(code: str) -> bool:
    return (code or "").strip().upper() in {"SC", "LC"}

def is_district(code: str) -> bool:
    return (code or "").strip().upper() in {"D", "DISTRICT"}

def pick_best_fuzzy(target_norm: str, choices: pd.Series, token_set_cut=92, partial_cut=96):
    """Find best fuzzy match"""
    if not target_norm:
        return (None, 0, "")
    uniq = choices.dropna().unique().tolist()
    if not uniq:
        return (None, 0, "")
    best1 = process.extractOne(target_norm, uniq, scorer=fuzz.token_set_ratio)
    if best1 and best1[1] >= token_set_cut:
        return (best1[0], best1[1], "token_set_ratio")
    best2 = process.extractOne(target_norm, uniq, scorer=fuzz.partial_ratio)
    if best2 and best2[1] >= partial_cut:
        return (best2[0], best2[1], "partial_ratio")
    return (None, 0, "")

def _clean(x):
    """Clean text fields"""
    return "" if pd.isna(x) else str(x).strip()

def generate_email_links(contacts_df, subject="", body=""):
    """Generate batch email links"""
    emails = []
    for _, row in contacts_df.iterrows():
        lea_type = _clean(row.get("LEA_TYPE", "")).upper()
        if lea_type in {"SC", "LC"}:
            rep_email = _clean(row.get("REPRESENTATIVE EMAIL", ""))
            fisc_email = _clean(row.get("CONTACT EMAIL", ""))
            if rep_email:
                emails.append(rep_email)
            if fisc_email and fisc_email != rep_email:
                emails.append(fisc_email)
        else:
            supt_email = _clean(row.get("SUPT. E-MAIL", ""))
            bm_email = _clean(row.get("BUS. MGR. E-MAIL", ""))
            if supt_email:
                emails.append(supt_email)
            if bm_email and bm_email != supt_email:
                emails.append(bm_email)
    
    # Remove duplicates and empty strings
    emails = [e for e in list(set(emails)) if e]
    
    # Create mailto link
    if emails:
        mailto = f"mailto:{';'.join(emails)}"
        if subject:
            mailto += f"?subject={urllib.parse.quote(subject)}"
        if body:
            separator = "&" if subject else "?"
            mailto += f"{separator}body={urllib.parse.quote(body)}"
        return emails, mailto
    return [], ""

# =========================
# Google Sheets Data Source
# =========================

# Google Sheets URLs (public sheets exported as CSV)
GOOGLE_SHEETS = {
    "districts": "https://docs.google.com/spreadsheets/d/1vkzVbwmg3LktPWlxK-SIi28hSIaP2YIG_wnp2FgWPYE/export?format=csv&gid=0",
    "charters": "https://docs.google.com/spreadsheets/d/1GQvRVXTwje6mhCyeZsGxUMH64RzAPOcstT24ANxcGK8/export?format=csv&gid=0",
    "assignments": "https://docs.google.com/spreadsheets/d/1uZY1Ep9jMpachr7MtBBy5Rwi25iVv80jp0X3i_e1ezg/export?format=csv&gid=1629654616"
}

@st.cache_data(ttl=3600)  # Cache for 1 hour
def load_data_from_sheets():
    """Load data directly from Google Sheets"""
    try:
        assign = pd.read_csv(GOOGLE_SHEETS["assignments"], dtype=str).fillna("")
        districts = pd.read_csv(GOOGLE_SHEETS["districts"], dtype=str).fillna("")
        charters = pd.read_csv(GOOGLE_SHEETS["charters"], dtype=str).fillna("")
        return assign, districts, charters
    except Exception as e:
        st.error(f"Error loading from Google Sheets: {e}")
        st.info("Make sure the sheets are shared as 'Anyone with the link can view'")
        raise

def process_assignments(assign):
    """Normalize assignment columns"""
    colmap = {
        "PED_NO": ["PED NO", "ped no", "PED_NO", "Ped No"],
        "LEA_TYPE": ["DISTRICT, STATE, OR LOCAL CHARTER", "LEA TYPE", "LEA", "TYPE"],
        "LEA_NAME": ["DISTRICT/CHARTER NAME", "LEA NAME", "NAME"]
    }
    
    for canon, candidates in colmap.items():
        found = None
        for c in candidates:
            if c in assign.columns:
                found = c
                break
        if not found:
            for c in assign.columns:
                if c.lower() == candidates[0].lower():
                    found = c
                    break
        if found:
            assign.rename(columns={found: canon}, inplace=True)
    
    assign["PED_NO"] = assign["PED_NO"].apply(ped_no_canonical)
    assign["LEA_TYPE"] = assign["LEA_TYPE"].str.strip().str.upper()
    assign["LEA_NAME_NORM"] = assign["LEA_NAME"].apply(normalize_name)
    
    return assign

def process_districts(districts):
    """Process district contacts"""
    district_id_options = ["ped no", "district no."]
    districts.columns = [
        ("PED NO" if c.strip().lower() in district_id_options else c) 
        for c in districts.columns
    ]
    districts["PED_NO"] = districts["PED NO"].apply(ped_no_canonical)
    return districts

def process_charters(charters):
    """Process charter contacts"""
    def get_col(df, wanted):
        for c in df.columns:
            if c.strip().lower() == wanted.strip().lower():
                return c
        return None
    
    TYPE_CANDIDATES = ["Charter Type", "CHARTER TYPE", "LEA Type", "Type"]
    NAME_CANDIDATES = ["CHARTER NAME", "School Name", "Organization Name", "Name"]
    
    ct_col = None
    for candidate in TYPE_CANDIDATES:
        ct_col = get_col(charters, candidate)
        if ct_col:
            break
    
    cn_col = None
    for candidate in NAME_CANDIDATES:
        cn_col = get_col(charters, candidate)
        if cn_col:
            break
    
    if ct_col and cn_col:
        charters["CHARTER TYPE"] = charters[ct_col].astype(str).str.strip().str.title()
        charters["CHARTER NAME"] = charters[cn_col].astype(str).str.strip()
        charters["CHARTER_NAME_NORM"] = charters["CHARTER NAME"].apply(normalize_name)
    
    return charters

def merge_data(assign, districts, charters, token_set_cut=92, partial_cut=96, strict_mode=False):
    """Merge assignment data with contacts"""
    
    # Split assignments
    assign_d = assign[assign["LEA_TYPE"].apply(is_district)].copy()
    assign_c = assign[assign["LEA_TYPE"].apply(is_charter)].copy()
    
    # Merge districts
    merged_d = assign_d.merge(districts, on="PED_NO", how="left", suffixes=("", "_DIST"))
    
    # Charter matching
    charter_index = assign_c[["PED_NO", "LEA_NAME", "LEA_NAME_NORM"]].drop_duplicates()
    name_to_ped = {}
    for _, row in charter_index.iterrows():
        base = row["LEA_NAME_NORM"]
        for var in smart_exact_variants(base):
            name_to_ped.setdefault(var, set()).add(row["PED_NO"])
    
    match_rows = []
    for i, r in charters.reset_index(drop=True).iterrows():
        ctype = (r.get("CHARTER TYPE", "") or "").strip().title()
        raw_name = r.get("CHARTER NAME", "")
        norm_name = r.get("CHARTER_NAME_NORM", "")
        
        cand_peds = name_to_ped.get(norm_name, set())
        if not cand_peds:
            for v in smart_exact_variants(norm_name):
                cand_peds |= name_to_ped.get(v, set())
        
        chosen_ped, method, score = None, "", 0
        
        if len(cand_peds) == 1:
            chosen_ped = list(cand_peds)[0]
            method = "exact/smart"
            score = 100
        elif not strict_mode and cand_peds:
            best_name, s, m = pick_best_fuzzy(
                norm_name, 
                charter_index["LEA_NAME_NORM"],
                token_set_cut=token_set_cut, 
                partial_cut=partial_cut
            )
            if best_name:
                ped_set = name_to_ped.get(best_name, set())
                if len(ped_set) == 1:
                    chosen_ped = next(iter(ped_set))
                    method = f"fuzzy ({m})"
                    score = s
        
        if chosen_ped:
            match_rows.append({
                "CHARTER CONTACT ROW": i,
                "matched_PED_NO": chosen_ped,
                "match_method": method,
                "match_score": score
            })
    
    match_log = pd.DataFrame(match_rows)
    merged_c = assign_c.copy()
    
    if not match_log.empty:
        merged_c = merged_c.merge(
            match_log[["matched_PED_NO", "match_method", "match_score"]],
            left_on="PED_NO",
            right_on="matched_PED_NO",
            how="left"
        )
        charters_with_row = charters.reset_index().rename(columns={"index": "CHARTER CONTACT ROW"})
        merged_c = merged_c.merge(
            charters_with_row,
            left_on="PED_NO",
            right_on=charters.apply(lambda r: ped_no_canonical(r.get("PED-NO", "")), axis=1),
            how="left",
            suffixes=("", "_CHARTER")
        )
    else:
        merged_c["match_method"] = ""
        merged_c["match_score"] = ""
    
    merged = pd.concat([merged_d, merged_c], ignore_index=True)
    
    # Add match indicators
    district_contact_cols = [
        "DISTRICT NAME", "SUPT. E-MAIL", "BUS. MGR. E-MAIL"
    ]
    has_district_contact = (
        merged[district_contact_cols].notna().any(axis=1)
        if all(c in merged.columns for c in district_contact_cols)
        else pd.Series([False]*len(merged), index=merged.index)
    )
    has_charter_match = merged.get("match_method", pd.Series([""]* len(merged))).fillna("").ne("")
    
    merged["Matched"] = (
        merged["LEA_TYPE"].str.upper().eq("D") & has_district_contact
    ) | (
        merged["LEA_TYPE"].str.upper().isin(["SC", "LC"]) & has_charter_match
    )
    
    return merged, match_log

# =========================
# Contact Display
# =========================

def display_contact_card(row: pd.Series):
    """Display contact information in a clean single-column format"""
    lea_type = _clean(row.get("LEA_TYPE", "")).upper()
    is_charter_org = lea_type in {"SC", "LC"}
    
    # Header
    col1, col2, col3 = st.columns([2, 1, 1])
    with col1:
        st.markdown(f"### {_clean(row.get('LEA_NAME', 'N/A'))}")
    with col2:
        st.metric("PED #", _clean(row.get("PED_NO", "N/A")))
    with col3:
        st.metric("Type", "Charter" if is_charter_org else "District")
    
    # Analyst Info
    st.markdown('<div class="contact-section">', unsafe_allow_html=True)
    st.markdown("**📊 Budget Analyst**")
    col1, col2 = st.columns(2)
    with col1:
        analyst = _clean(row.get("Analyst", "N/A"))
        st.write(f"**Name:** {analyst}")
        analyst_email = _clean(row.get("Analyst Email", "N/A"))
        if analyst_email != "N/A":
            st.write(f"**Email:** [{analyst_email}](mailto:{analyst_email})")
    with col2:
        supervisor = _clean(row.get("Analyst Reports To", "N/A"))
        st.write(f"**Supervisor:** {supervisor}")
        analyst_phone = _clean(row.get("Analyst Phone", "N/A"))
        st.write(f"**Phone:** {analyst_phone}")
    st.markdown('</div>', unsafe_allow_html=True)
    
    if is_charter_org:
        # Charter Representative
        st.markdown('<div class="contact-section">', unsafe_allow_html=True)
        st.markdown("**👤 Charter Representative**")
        
        rep_prefix = _clean(row.get("CHARTER REP PREFIX", ""))
        rep_first = _clean(row.get("CHARTER REP FIRST NAME", ""))
        rep_last = _clean(row.get("CHARTER REP LAST NAME", ""))
        rep_name = f"{rep_prefix} {rep_first} {rep_last}".strip() or "N/A"
        rep_title = _clean(row.get("CHARTER REP TITLE", "N/A"))
        rep_phone = _clean(row.get("REPRESENTATIVE CONTACT PHONE #", "")) or _clean(row.get("PHONE", "N/A"))
        rep_email = _clean(row.get("REPRESENTATIVE EMAIL", "N/A"))
        
        st.write(f"**Name:** {rep_name}")
        st.write(f"**Title:** {rep_title}")
        st.write(f"**Phone:** {rep_phone}")
        if rep_email != "N/A":
            st.write(f"**Email:** [{rep_email}](mailto:{rep_email})")
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Fiscal Contact
        st.markdown('<div class="contact-section">', unsafe_allow_html=True)
        st.markdown("**💰 Fiscal Contact**")
        
        fisc_prefix = _clean(row.get("FISCAL CONTACT PREFIX", ""))
        fisc_first = _clean(row.get("FISCAL CONTACT FIRST NAME", ""))
        fisc_last = _clean(row.get("FISCAL CONTACT LAST NAME", ""))
        fisc_name = f"{fisc_prefix} {fisc_first} {fisc_last}".strip() or "N/A"
        fisc_title = _clean(row.get("CONTACT TITLE", "N/A"))
        fisc_phone = _clean(row.get("FISCAL CONTACT PHONE #", "N/A"))
        fisc_email = _clean(row.get("CONTACT EMAIL", "N/A"))
        
        st.write(f"**Name:** {fisc_name}")
        st.write(f"**Title:** {fisc_title}")
        st.write(f"**Phone:** {fisc_phone}")
        if fisc_email != "N/A":
            st.write(f"**Email:** [{fisc_email}](mailto:{fisc_email})")
        st.markdown('</div>', unsafe_allow_html=True)
        
    else:
        # Superintendent
        st.markdown('<div class="contact-section">', unsafe_allow_html=True)
        st.markdown("**👤 Superintendent**")
        
        supt_prefix = _clean(row.get("SUPT. PREFIX", ""))
        supt_first = _clean(row.get("SUPT. FIRST & M.I.", ""))
        supt_last = _clean(row.get("SUPT. LAST NAME", ""))
        supt_name = f"{supt_prefix} {supt_first} {supt_last}".strip() or "N/A"
        supt_title = _clean(row.get("SUPT. TITLE", "N/A"))
        supt_phone = _clean(row.get("SUPT. PHONE", "N/A"))
        supt_email = _clean(row.get("SUPT. E-MAIL", "N/A"))
        
        st.write(f"**Name:** {supt_name}")
        st.write(f"**Title:** {supt_title}")
        st.write(f"**Phone:** {supt_phone}")
        if supt_email != "N/A":
            st.write(f"**Email:** [{supt_email}](mailto:{supt_email})")
        st.markdown('</div>', unsafe_allow_html=True)
        
        # Business Manager
        st.markdown('<div class="contact-section">', unsafe_allow_html=True)
        st.markdown("**💰 Business Manager**")
        
        bm_prefix = _clean(row.get("BUS. MGR. PREFIX", ""))
        bm_first = _clean(row.get("BUS. MGR. FIRST & M. I.", ""))
        bm_last = _clean(row.get("BUS. MGR. LAST NAME", ""))
        bm_name = f"{bm_prefix} {bm_first} {bm_last}".strip() or "N/A"
        bm_title = _clean(row.get("BUS. MGR. TITLE", "N/A"))
        bm_phone = _clean(row.get("BUS. MGR. PHONE", "N/A"))
        bm_email = _clean(row.get("BUS. MGR. E-MAIL", "N/A"))
        
        st.write(f"**Name:** {bm_name}")
        st.write(f"**Title:** {bm_title}")
        st.write(f"**Phone:** {bm_phone}")
        if bm_email != "N/A":
            st.write(f"**Email:** [{bm_email}](mailto:{bm_email})")
        st.markdown('</div>', unsafe_allow_html=True)
    
    # Address (if available)
    addr_parts = []
    mailing = _clean(row.get("MAILING ADDRESS", ""))
    city = _clean(row.get("CITY", ""))
    state = _clean(row.get("ST", "") or row.get("STATE", ""))
    zip_code = _clean(row.get("ZIP", ""))
    
    if mailing:
        addr_parts.append(mailing)
    if city or state or zip_code:
        cityline = f"{city}, {state} {zip_code}".strip(", ")
        if cityline:
            addr_parts.append(cityline)
    
    if addr_parts:
        st.markdown('<div class="contact-section">', unsafe_allow_html=True)
        st.markdown("**📍 Address**")
        for part in addr_parts:
            st.write(part)
        st.markdown('</div>', unsafe_allow_html=True)

# =========================
# Main App
# =========================

st.title("🏫 PED Contacts Manager")
st.markdown("*Manage school district and charter school contacts with ease*")

# Sidebar - Data Source Info
st.sidebar.header("📁 Data Source")
st.sidebar.info("📊 Loading live data from Google Sheets")

# Matching options
with st.sidebar.expander("⚙️ Matching Settings"):
    token_set_cut = st.slider("Token set threshold", 80, 100, 92, 1)
    partial_cut = st.slider("Partial match threshold", 90, 100, 96, 1)
    strict_mode = st.checkbox("Strict mode (exact matches only)", value=False)

# Add refresh button
if st.sidebar.button("🔄 Refresh Data from Sheets"):
    st.cache_data.clear()
    st.rerun()

# Load data from Google Sheets
try:
    with st.spinner("Loading data from Google Sheets..."):
        assign, districts, charters = load_data_from_sheets()
        st.sidebar.success("✅ Data loaded successfully")
except Exception as e:
    st.error(f"❌ Failed to load data: {e}")
    st.stop()

# Process data
assign = process_assignments(assign)
districts = process_districts(districts)
charters = process_charters(charters)
merged, match_log = merge_data(assign, districts, charters, token_set_cut, partial_cut, strict_mode)

# Initialize session state for filters
if "filters_initialized" not in st.session_state:
    st.session_state.analyst_sel = sorted(merged["Analyst"].dropna().unique().tolist())
    st.session_state.sup_sel = sorted(merged["Analyst Reports To"].dropna().unique().tolist())
    st.session_state.only_unmatched = False
    st.session_state.selected_rows = []
    st.session_state.filters_initialized = True

# Sidebar - Filters
with st.sidebar.expander("🔍 Filters", expanded=True):
    col1, col2 = st.columns(2)
    with col1:
        if st.button("🔄 Reset"):
            st.session_state.analyst_sel = sorted(merged["Analyst"].dropna().unique().tolist())
            st.session_state.sup_sel = sorted(merged["Analyst Reports To"].dropna().unique().tolist())
            st.session_state.only_unmatched = False
            st.rerun()
    with col2:
        show_unmatched = st.checkbox("Only unmatched", value=st.session_state.only_unmatched)
    
    analysts = st.multiselect(
        "Analyst",
        options=sorted(merged["Analyst"].dropna().unique().tolist()),
        default=st.session_state.analyst_sel
    )
    
    supervisors = st.multiselect(
        "Supervisor",
        options=sorted(merged["Analyst Reports To"].dropna().unique().tolist()),
        default=st.session_state.sup_sel
    )
    
    st.session_state.analyst_sel = analysts
    st.session_state.sup_sel = supervisors
    st.session_state.only_unmatched = show_unmatched

# Apply filters
filtered = merged.copy()
if analysts:
    filtered = filtered[filtered["Analyst"].isin(analysts)]
if supervisors:
    filtered = filtered[filtered["Analyst Reports To"].isin(supervisors)]
if show_unmatched:
    filtered = filtered[~filtered["Matched"]]

# Search
st.subheader("🔎 Search")
search_query = st.text_input("Search all fields", placeholder="Type to search...")

if search_query:
    search_norm = unidecode(search_query).lower()
    str_cols = filtered.select_dtypes(include="object").columns.tolist()
    mask = pd.Series(False, index=filtered.index)
    for c in str_cols:
        col = filtered[c].astype(str).map(lambda x: unidecode(x).lower())
        mask |= col.str.contains(search_norm, na=False)
    filtered = filtered[mask]

# Batch Email Section
if len(filtered) > 0:
    with st.expander("📧 Batch Email", expanded=False):
        st.markdown('<div class="batch-email-section">', unsafe_allow_html=True)
        
        # Selection options
        email_mode = st.radio(
            "Select recipients:",
            ["Current view (all filtered)", "Specific LEAs", "By analyst"]
        )
        
        selection_df = filtered.copy()
        
        if email_mode == "Specific LEAs":
            lea_options = sorted(filtered["LEA_NAME"].unique().tolist())
            selected_leas = st.multiselect("Choose LEAs:", lea_options)
            if selected_leas:
                selection_df = filtered[filtered["LEA_NAME"].isin(selected_leas)]
        elif email_mode == "By analyst":
            analyst_options = sorted(filtered["Analyst"].dropna().unique().tolist())
            selected_analyst = st.selectbox("Choose analyst:", analyst_options)
            if selected_analyst:
                selection_df = filtered[filtered["Analyst"] == selected_analyst]
        
        # Email composition
        col1, col2 = st.columns(2)
        with col1:
            email_subject = st.text_input("Email subject:", "")
        with col2:
            st.metric("Recipients", len(selection_df))
        
        email_body = st.text_area("Email body (optional):", height=100)
        
        if st.button("📨 Generate Email Link"):
            emails, mailto_link = generate_email_links(selection_df, email_subject, email_body)
            if emails:
                st.success(f"✅ Generated email for {len(emails)} recipients")
                st.markdown(f"**Recipients:** {', '.join(emails)}")
                st.markdown(f"[Click here to open email client]({mailto_link})")
                
                # Also provide copy-paste option
                with st.expander("📋 Copy email addresses"):
                    st.code("; ".join(emails))
            else:
                st.warning("⚠️ No email addresses found in selection")
        
        st.markdown('</div>', unsafe_allow_html=True)

# Metrics
col1, col2, col3, col4 = st.columns(4)
with col1:
    st.metric("Total LEAs", len(merged))
with col2:
    st.metric("Filtered", len(filtered))
with col3:
    st.metric("✅ Matched", int(filtered["Matched"].sum()))
with col4:
    st.metric("❌ Unmatched", int((~filtered["Matched"]).sum()))

# Contact Details
if len(filtered) > 0:
    # Create selection dropdown
    filtered_with_id = filtered.copy()
    filtered_with_id.insert(0, "_row_id", filtered_with_id.index)
    filtered_with_id.set_index("_row_id", inplace=True)
    
    def row_label(r):
        ped = _clean(r.get("PED_NO", ""))
        name = _clean(r.get("LEA_NAME", ""))
        return f"{ped} — {name}"
    
    options = filtered_with_id.index.tolist()
    labels = {idx: row_label(row) for idx, row in filtered_with_id.iterrows()}
    
    selected_id = st.selectbox(
        "📋 Select an LEA:",
        options=options,
        format_func=lambda x: labels.get(x, str(x)),
        key="lea_selector"
    )
    
    if selected_id is not None:
        selected_row = filtered_with_id.loc[selected_id]
        display_contact_card(selected_row)
else:
    st.info("No results match your current filters")

# Data Table
with st.expander("📊 View Data Table", expanded=False):
    display_cols = [
        "PED_NO", "LEA_NAME", "LEA_TYPE", "Analyst", 
        "Analyst Reports To", "Matched"
    ]
    display_cols = [c for c in display_cols if c in filtered.columns]
    st.dataframe(
        filtered[display_cols],
        use_container_width=True,
        height=400
    )

# Downloads
st.subheader("💾 Downloads")
col1, col2, col3 = st.columns(3)

with col1:
    csv = filtered.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ Filtered Results",
        data=csv,
        file_name="filtered_contacts.csv",
        mime="text/csv"
    )

with col2:
    csv_all = merged.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ All Contacts",
        data=csv_all,
        file_name="all_contacts.csv",
        mime="text/csv"
    )

with col3:
    csv_log = match_log.to_csv(index=False).encode("utf-8-sig")
    st.download_button(
        "⬇️ Match Log",
        data=csv_log,
        file_name="match_log.csv",
        mime="text/csv"
    )

# Footer
st.markdown("---")
st.caption("New Mexico Public Education Department | School Budget Bureau")