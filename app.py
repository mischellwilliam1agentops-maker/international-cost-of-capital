"""
International Cost of Capital App
---------------------------------
Compares a project's cost of equity, cost of debt and WACC in USD and in the
currency of a selected country.

Data (fetched live, cached, refreshable):
  * Industry unlevered betas ......... Aswath Damodaran, NYU Stern (betas.xls)
  * BBB (Baa2) USD default spread .... Aswath Damodaran, NYU Stern (ratings.html)
  * Statutory corporate tax rates .... OECD Corporate Tax Statistics database
  * 10-year government bond yields ... Trading Economics (tradingeconomics.com/bonds)

Every fetched number can be overwritten with a manual input in the sidebar.
"""

import datetime as dt
import io
import re
import unicodedata

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st
from bs4 import BeautifulSoup

# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------
st.set_page_config(page_title="International Cost of Capital", page_icon="🌍", layout="wide")

BETA_URL = "https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls"
BETA_HTML_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/Betas.html"
RATINGS_URL = "https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html"
OECD_PAGE_URL = (
    "https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/"
    "full-report/statutory-corporate-income-tax-rates_440f9286.html"
)
OECD_API_URL = (
    "https://sdmx.oecd.org/public/rest/data/OECD.CTP.TPS,DSD_TAX_CIT@DF_CIT,/all"
    "?startPeriod=2018&dimensionAtObservation=AllDimensions&format=csvfilewithlabels"
)
TE_BONDS_URL = "https://tradingeconomics.com/bonds"
TE_COUNTRY_URL = "https://tradingeconomics.com/{slug}/government-bond-yield"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}
CACHE_TTL = 6 * 60 * 60  # 6 hours

DEFAULT_INDUSTRY = "Software (System & Application)"
DEFAULT_COUNTRY = "United States"
US = "United States"

# Fallback snapshot used ONLY if the OECD fetch fails (combined statutory CIT
# rates, 2025, OECD Corporate Tax Statistics as reported by Tax Foundation/KPMG).
TAX_FALLBACK = {
    "United States": 25.57, "United Kingdom": 25.0, "Germany": 29.9, "France": 25.8,
    "Italy": 27.8, "Austria": 23.0, "Belgium": 25.0, "Denmark": 22.0, "Finland": 20.0,
    "Greece": 22.0, "Ireland": 12.5, "Hungary": 9.0, "Czechia": 21.0, "Estonia": 22.0,
    "Iceland": 21.0, "Latvia": 20.0, "Lithuania": 16.0, "Bulgaria": 10.0, "Croatia": 18.0,
    "Cyprus": 12.5,
}
TAX_FALLBACK_NOTE = "Fallback snapshot: OECD combined statutory CIT rates, 2025"

# Fallback for the BBB spread if ratings.html cannot be read (verified Jan-2026 table).
BBB_FALLBACK = (1.11, "January 2026 (fallback snapshot)")

# Trading Economics slugs used if the /bonds overview page cannot be read.
TE_FALLBACK_SLUGS = [
    "united-states", "united-kingdom", "germany", "france", "italy", "spain", "japan",
    "china", "india", "brazil", "mexico", "canada", "australia", "south-korea",
    "switzerland", "netherlands", "sweden", "norway", "denmark", "poland", "turkey",
    "south-africa", "indonesia", "singapore", "new-zealand", "chile", "colombia",
    "philippines", "thailand", "malaysia", "israel", "ireland", "portugal", "greece",
    "austria", "belgium", "finland", "czech-republic", "hungary", "peru",
]

# Trading Economics name -> OECD jurisdiction name (where they differ)
OECD_ALIASES = {
    "south korea": "korea",
    "turkey": "turkiye",
    "czech republic": "czechia",
    "china": "china (people's republic of)",
    "slovakia": "slovak republic",
    "russia": "russian federation",
    "vietnam": "viet nam",
    "hong kong": "hong kong (china)",
    "taiwan": "chinese taipei",
    "ivory coast": "cote d'ivoire",
    "egypt": "egypt",
    "uae": "united arab emirates",
}

CURRENCY = {
    "United States": "USD", "United Kingdom": "GBP", "Euro Area": "EUR", "Germany": "EUR",
    "France": "EUR", "Italy": "EUR", "Spain": "EUR", "Netherlands": "EUR", "Belgium": "EUR",
    "Austria": "EUR", "Finland": "EUR", "Ireland": "EUR", "Portugal": "EUR", "Greece": "EUR",
    "Slovakia": "EUR", "Slovenia": "EUR", "Lithuania": "EUR", "Latvia": "EUR", "Estonia": "EUR",
    "Croatia": "EUR", "Cyprus": "EUR", "Malta": "EUR", "Japan": "JPY", "China": "CNY",
    "India": "INR", "Brazil": "BRL", "Mexico": "MXN", "Canada": "CAD", "Australia": "AUD",
    "South Korea": "KRW", "Switzerland": "CHF", "Sweden": "SEK", "Norway": "NOK",
    "Denmark": "DKK", "Poland": "PLN", "Turkey": "TRY", "South Africa": "ZAR",
    "Indonesia": "IDR", "Singapore": "SGD", "New Zealand": "NZD", "Chile": "CLP",
    "Colombia": "COP", "Philippines": "PHP", "Thailand": "THB", "Malaysia": "MYR",
    "Israel": "ILS", "Czech Republic": "CZK", "Hungary": "HUF", "Peru": "PEN",
    "Hong Kong": "HKD", "Taiwan": "TWD", "Vietnam": "VND", "Russia": "RUB",
    "Egypt": "EGP", "Nigeria": "NGN", "Kenya": "KES", "Pakistan": "PKR", "Romania": "RON",
    "Iceland": "ISK", "Argentina": "ARS", "Saudi Arabia": "SAR", "Qatar": "QAR",
    "Bangladesh": "BDT", "Sri Lanka": "LKR", "Morocco": "MAD", "Serbia": "RSD",
    "Bulgaria": "BGN", "Ukraine": "UAH", "Kazakhstan": "KZT", "Uganda": "UGX",
}


def now_str() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _get(url: str, timeout: int = 30) -> requests.Response:
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s.strip().lower())


def _to_float(x):
    if x is None:
        return np.nan
    if isinstance(x, (int, float, np.number)):
        return float(x)
    s = str(x).replace("%", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return np.nan


# ----------------------------------------------------------------------------
# Data fetchers (each returns a dict; exceptions are caught by the loaders)
# ----------------------------------------------------------------------------
def _parse_beta_table(raw: pd.DataFrame):
    """Find the 'Industry Name' header row and extract the industry table."""
    raw = raw.copy()
    hdr_row = None
    for i in range(min(len(raw), 40)):
        vals = [_norm(v) for v in raw.iloc[i].tolist()]
        if "industry name" in vals:
            hdr_row = i
            break
    if hdr_row is None:
        return None, None
    header = [_norm(v) for v in raw.iloc[hdr_row].tolist()]
    ind_col = header.index("industry name")
    # exact "unlevered beta" (NOT the cash-corrected version)
    ub_col = None
    for j, h in enumerate(header):
        if h == "unlevered beta":
            ub_col = j
            break
    if ub_col is None:
        for j, h in enumerate(header):
            if "unlevered beta" in h and "cash" not in h:
                ub_col = j
                break
    if ub_col is None:
        return None, None

    def find(label):
        for j, h in enumerate(header):
            if h.startswith(label):
                return j
        return None

    cols = {"Industry": ind_col, "Unlevered beta": ub_col, "Number of firms": find("number of firms"),
            "Levered beta (Damodaran)": find("beta"), "D/E ratio (Damodaran)": find("d/e")}
    body = raw.iloc[hdr_row + 1:]
    out = pd.DataFrame({k: body.iloc[:, v].values for k, v in cols.items() if v is not None})
    out["Industry"] = out["Industry"].astype(str).str.strip()
    out = out[out["Industry"].ne("") & out["Industry"].ne("nan")]
    for c in out.columns:
        if c != "Industry":
            out[c] = out[c].map(_to_float)
    out = out.dropna(subset=["Unlevered beta"]).reset_index(drop=True)

    # date: look in the rows above the header for a 'date' label
    date = None
    for i in range(hdr_row):
        row = raw.iloc[i].tolist()
        for j, v in enumerate(row):
            if isinstance(v, str) and "date" in v.lower():
                for w in row[j + 1:]:
                    if pd.notna(w) and str(w).strip():
                        date = w
                        break
                if date is None:
                    m = re.search(r"([A-Z][a-z]+ \d{1,2},? \d{4}|[A-Z][a-z]+ \d{4}|\d{1,2}/\d{1,2}/\d{2,4})", v)
                    date = m.group(1) if m else None
            if date is not None:
                break
        if date is not None:
            break
    if isinstance(date, (dt.datetime, pd.Timestamp)):
        date = pd.Timestamp(date).strftime("%Y-%m-%d")
    elif isinstance(date, (int, float)) and 30000 < float(date) < 60000:  # Excel serial date
        date = (dt.datetime(1899, 12, 30) + dt.timedelta(days=float(date))).strftime("%Y-%m-%d")
    return out, (str(date) if date is not None else None)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_betas():
    errors = []
    try:
        r = _get(BETA_URL, timeout=45)
        sheets = pd.read_excel(io.BytesIO(r.content), sheet_name=None, header=None)
        for name, raw in sheets.items():
            df, date = _parse_beta_table(raw)
            if df is not None and len(df) > 10:
                return {"df": df, "date": date or "see file header", "source": BETA_URL,
                        "fetched": now_str(), "note": f"sheet '{name}'"}
        errors.append("betas.xls: industry table not found")
    except Exception as e:  # noqa: BLE001
        errors.append(f"betas.xls: {e}")
    # fallback: HTML version of the same dataset
    r = _get(BETA_HTML_URL, timeout=45)
    for t in pd.read_html(io.StringIO(r.text), header=None):
        df, date = _parse_beta_table(t)
        if df is not None and len(df) > 10:
            m = re.search(r"(?:Date|updated)[^A-Za-z0-9]*([A-Z][a-z]+ \d{1,2},? \d{4}|[A-Z][a-z]+ \d{4})", r.text)
            return {"df": df, "date": (m.group(1) if m else "see page"), "source": BETA_HTML_URL,
                    "fetched": now_str(), "note": "HTML fallback; " + "; ".join(errors)}
    raise RuntimeError("Could not parse Damodaran betas. " + "; ".join(errors))


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_bbb_spread():
    r = _get(RATINGS_URL)
    soup = BeautifulSoup(r.text, "lxml")
    spread = None
    for tr in soup.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        for i, c in enumerate(cells):
            if re.sub(r"\s", "", c).lower() == "baa2/bbb":
                # first occurrence in the row = large non-financial service firms block
                for nxt in cells[i + 1:]:
                    val = _to_float(nxt)
                    if not np.isnan(val):
                        spread = val
                        break
                break
        if spread is not None:
            break
    if spread is None:
        raise RuntimeError("Baa2/BBB row not found on ratings.html")
    if spread < 0.2:  # stored as a decimal, convert to %
        spread *= 100
    text = soup.get_text(" ", strip=True)
    m = re.search(r"as of ([A-Z][a-z]+ \d{4})", text) or re.search(
        r"((?:January|February|March|April|May|June|July|August|September|October|November|December) \d{4})", text)
    return {"spread_pct": spread, "date": m.group(1) if m else "see page", "source": RATINGS_URL,
            "fetched": now_str()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_tax_rates():
    """Latest combined (central + sub-central) statutory CIT rate per jurisdiction."""
    r = _get(OECD_API_URL, timeout=60)
    df = pd.read_csv(io.StringIO(r.text), low_memory=False)
    cols = {c.lower(): c for c in df.columns}
    area_code = cols.get("ref_area")
    area_name = cols.get("reference area") or area_code
    time_col = cols.get("time_period")
    val_col = cols.get("obs_value")
    if not all([area_code, time_col, val_col]):
        raise RuntimeError(f"Unexpected OECD columns: {list(df.columns)[:15]}")
    skip = {area_code, area_name, time_col, val_col}
    label_cols = [c for c in df.columns if c not in skip and not pd.api.types.is_numeric_dtype(df[c])]
    sig = df[label_cols].astype(str).fillna("").agg(" | ".join, axis=1)
    sig_l = sig.str.lower()
    df["_sig"] = sig
    df["_val"] = pd.to_numeric(df[val_col], errors="coerce")
    df[time_col] = pd.to_numeric(df[time_col].astype(str).str[:4], errors="coerce")
    unit_col = next((c for c in df.columns if c.lower() in ("unit_measure", "unit of measure")), None)
    # keep percentage rates only; drop targeted / small-business rates, thresholds and partial measures
    bad = sig_l.str.contains("target|small|threshold|less deduction|exclusive|excl|sub-central|subcentral|sub_cent|"
                             "sub-national|reduced|preferential|currency|national currency")
    ok = ~bad & df["_val"].between(0, 60)
    if unit_col is not None:
        u = df[unit_col].astype(str).str.lower()
        ok &= u.str.contains("pt|percent|%") | u.eq("nan")
    pool = df[ok].dropna(subset=["_val", time_col])
    if pool.empty:
        raise RuntimeError("No usable CIT rate rows in OECD data. Columns: " + ", ".join(map(str, df.columns)))
    comb = pool[pool["_sig"].str.lower().str.contains(r"combined|\bcomb")]
    if not comb.empty:
        best = comb.groupby("_sig")[area_code].nunique().sort_values(ascending=False).index[0]
        cand = comb[comb["_sig"] == best]
        series_note = best
    else:
        # combined (central + sub-central) is the highest standard statutory rate for each country-year
        cand = pool.sort_values("_val").groupby([area_code, time_col]).tail(1)
        series_note = "highest standard statutory CIT rate per country-year (= combined rate); series: " + \
            "; ".join(pool["_sig"].value_counts().index[:4])
    cand = cand.sort_values(time_col).groupby(area_code).tail(1)
    cand = cand.assign(**{val_col: cand["_val"]})
    best = series_note
    out = pd.DataFrame({
        "Country": cand[area_name].astype(str).values,
        "ISO3": cand[area_code].astype(str).values,
        "Tax rate (%)": pd.to_numeric(cand[val_col], errors="coerce").values,
        "Year": cand[time_col].astype("Int64").values,
    }).dropna(subset=["Tax rate (%)"])
    latest = int(out["Year"].max())
    return {"df": out.reset_index(drop=True), "date": f"{latest} (latest year in OECD database)",
            "source": OECD_PAGE_URL + "  |  data: " + OECD_API_URL.split("?")[0],
            "fetched": now_str(), "series": best}


def _slug_to_name(slug: str) -> str:
    name = slug.replace("-", " ").title()
    return {"United States": "United States", "Euro Area": "Euro Area"}.get(name, name)


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_yields_overview():
    """Scrape the 10Y benchmark yield of every country on tradingeconomics.com/bonds."""
    r = _get(TE_BONDS_URL)
    soup = BeautifulSoup(r.text, "lxml")
    rows = []
    seen = set()
    for tr in soup.find_all("tr"):
        a = tr.find("a", href=re.compile(r"/[a-z0-9\-]+/government-bond-yield"))
        if a is None:
            continue
        slug = re.search(r"/([a-z0-9\-]+)/government-bond-yield", a["href"]).group(1)
        if slug in seen:
            continue
        tds = tr.find_all("td")
        texts = [td.get_text(" ", strip=True) for td in tds]
        link_idx = next((i for i, td in enumerate(tds) if td.find("a") is a or a in td.descendants), 0)
        y = np.nan
        for t in texts[link_idx + 1:]:
            y = _to_float(t)
            if not np.isnan(y):
                break
        if np.isnan(y):
            continue
        date = texts[-1] if texts else ""
        seen.add(slug)
        rows.append({"Country": _slug_to_name(slug), "slug": slug, "10Y yield (%)": y, "Date": date})
    df = pd.DataFrame(rows)
    if len(df) < 5 or US not in set(df.get("Country", [])):
        raise RuntimeError("Could not parse the Trading Economics bonds table")
    return {"df": df, "date": "Trading Economics quote date per country (see table)", "source": TE_BONDS_URL,
            "fetched": now_str()}


@st.cache_data(ttl=CACHE_TTL, show_spinner=False)
def fetch_yield_country(slug: str):
    """Fallback: read one country's 10Y yield from its Trading Economics page."""
    r = _get(TE_COUNTRY_URL.format(slug=slug))
    soup = BeautifulSoup(r.text, "lxml")
    meta = soup.find("meta", attrs={"name": "description"}) or soup.find("meta", attrs={"property": "og:description"})
    text = (meta.get("content", "") if meta else "") + " " + soup.get_text(" ", strip=True)[:4000]
    m = re.search(r"(?:to|at|was)\s+(-?\d+(?:\.\d+)?)\s*(?:%|percent)", text)
    if not m:
        raise RuntimeError(f"No yield found on {slug} page")
    d = re.search(r"on ([A-Z][a-z]+ \d{1,2}, \d{4})", text)
    return {"yield": float(m.group(1)), "date": d.group(1) if d else "see page",
            "source": TE_COUNTRY_URL.format(slug=slug), "fetched": now_str()}


def safe(fn, *args):
    try:
        return fn(*args), None
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


# ----------------------------------------------------------------------------
# Finance
# ----------------------------------------------------------------------------
def convert_usd_rate(r_usd: float, y_us: float, y_fc: float, method: str) -> float:
    """Convert a USD rate into the foreign currency using the yield differential."""
    if method.startswith("Multiplicative"):
        return (1 + r_usd) * (1 + y_fc) / (1 + y_us) - 1
    return r_usd + (y_fc - y_us)


def compute(beta_u, tax, d, erp, y_us, y_fc, s_usd, s_fc_override=None, method="Multiplicative"):
    """All inputs as decimals. Returns a dict of results (decimals)."""
    e = 1.0 - d
    de = d / e
    beta_l = beta_u * (1 + (1 - tax) * de)
    # USD
    re_usd = y_us + beta_l * erp
    rd_usd = y_us + s_usd
    wacc_usd = e * re_usd + d * rd_usd * (1 - tax)
    # Foreign currency
    re_fc = convert_usd_rate(re_usd, y_us, y_fc, method)
    s_fc_auto = convert_usd_rate(rd_usd, y_us, y_fc, method) - y_fc
    s_fc = s_fc_auto if s_fc_override is None else s_fc_override
    rd_fc = y_fc + s_fc
    wacc_fc = e * re_fc + d * rd_fc * (1 - tax)
    return dict(beta_u=beta_u, beta_l=beta_l, de=de, e=e, d=d, tax=tax, erp=erp,
                y_us=y_us, y_fc=y_fc, s_usd=s_usd, s_fc_auto=s_fc_auto, s_fc=s_fc,
                re_usd=re_usd, rd_usd=rd_usd, rd_usd_at=rd_usd * (1 - tax), wacc_usd=wacc_usd,
                re_fc=re_fc, rd_fc=rd_fc, rd_fc_at=rd_fc * (1 - tax), wacc_fc=wacc_fc)


def pct(x, nd=2):
    return "n/a" if x is None or (isinstance(x, float) and np.isnan(x)) else f"{x * 100:.{nd}f}%"


# ----------------------------------------------------------------------------
# Load data
# ----------------------------------------------------------------------------
st.title("🌍 International Cost of Capital")
st.caption("Project cost of equity, cost of debt and WACC in USD and in a selected country's currency.")

with st.sidebar:
    st.header("Data")
    if st.button("🔄 Refresh data fetch", width="stretch",
                 help="Clear the cache and re-download all sources"):
        st.cache_data.clear()
        for k in list(st.session_state.keys()):
            if k.startswith("ov_val_"):
                del st.session_state[k]
        st.rerun()

with st.spinner("Fetching betas, spreads, tax rates and bond yields..."):
    betas, err_beta = safe(fetch_betas)
    bbb, err_bbb = safe(fetch_bbb_spread)
    tax, err_tax = safe(fetch_tax_rates)
    yov, err_yov = safe(fetch_yields_overview)

# --- tax table (live or fallback) ---
if tax is not None:
    tax_df = tax["df"]
    tax_src = tax
else:
    tax_df = pd.DataFrame({"Country": list(TAX_FALLBACK), "Tax rate (%)": list(TAX_FALLBACK.values()),
                           "Year": 2025})
    tax_src = {"date": TAX_FALLBACK_NOTE, "source": OECD_PAGE_URL, "fetched": "not fetched"}

tax_lookup = {_norm(c): (v, y) for c, v, y in zip(tax_df["Country"], tax_df["Tax rate (%)"], tax_df["Year"])}


def tax_for(country: str):
    key = _norm(country)
    for k in (key, OECD_ALIASES.get(key, "")):
        if k and k in tax_lookup:
            return tax_lookup[k]
    for k, v in tax_lookup.items():  # loose match, e.g. "china (people's republic of)"
        if k.startswith(key + " ") or k.startswith(key + "("):
            return v
    return (np.nan, None)


# --- yields: country list ---
if yov is not None:
    yields_df = yov["df"]
else:
    yields_df = pd.DataFrame({"Country": [_slug_to_name(s) for s in TE_FALLBACK_SLUGS],
                              "slug": TE_FALLBACK_SLUGS, "10Y yield (%)": np.nan, "Date": ""})
countries = sorted(yields_df["Country"].unique(), key=lambda c: (c != US, c))


def yield_for(country: str):
    """Return (yield %, date, source, error)."""
    row = yields_df[yields_df["Country"] == country]
    if not row.empty and pd.notna(row["10Y yield (%)"].iloc[0]):
        return row["10Y yield (%)"].iloc[0], row["Date"].iloc[0], TE_BONDS_URL, None
    slug = row["slug"].iloc[0] if not row.empty else country.lower().replace(" ", "-")
    res, err = safe(fetch_yield_country, slug)
    if res:
        return res["yield"], res["date"], res["source"], None
    return np.nan, None, TE_COUNTRY_URL.format(slug=slug), err


# ----------------------------------------------------------------------------
# Sidebar inputs
# ----------------------------------------------------------------------------
with st.sidebar:
    st.header("Project settings")
    industries = betas["df"]["Industry"].tolist() if betas else [DEFAULT_INDUSTRY]
    ind_index = industries.index(DEFAULT_INDUSTRY) if DEFAULT_INDUSTRY in industries else 0
    industry = st.selectbox("Industry (Damodaran U.S. industry)", industries, index=ind_index)
    country = st.selectbox("Selected country", countries,
                           index=countries.index(DEFAULT_COUNTRY) if DEFAULT_COUNTRY in countries else 0)
    tax_choice = st.radio("Project tax rate", ["U.S. tax rate", "Selected country's tax rate"], index=0,
                          horizontal=True, help="One tax rate is used for both the USD and foreign-currency calculations")
    erp_pct = st.number_input("Equity risk premium (%)", min_value=0.0, max_value=20.0, value=6.5, step=0.1)
    debt_pct = st.slider("Target debt share D/(D+E) (%)", min_value=0, max_value=90, value=30, step=1)
    method = st.radio("Currency conversion of USD rates",
                      ["Multiplicative (exact parity)", "Additive (approximation)"], index=0,
                      help="Multiplicative: (1+r_FC) = (1+r_USD)(1+y_FC)/(1+y_US). "
                           "Additive: r_FC = r_USD + (y_FC - y_US). Pick the one used in class.")

# Fetched values
beta_row = betas["df"][betas["df"]["Industry"] == industry] if betas else pd.DataFrame()
beta_fetched = float(beta_row["Unlevered beta"].iloc[0]) if not beta_row.empty else np.nan
tax_us_fetched, tax_us_year = tax_for(US)
tax_fc_fetched, tax_fc_year = tax_for(country)
s_usd_fetched = bbb["spread_pct"] if bbb else BBB_FALLBACK[0]
y_us_fetched, y_us_date, y_us_src, y_us_err = yield_for(US)
y_fc_fetched, y_fc_date, y_fc_src, y_fc_err = yield_for(country)


def override(label, key, fetched, default_if_missing, step=0.01, fmt="%.2f", help_txt=None):
    """Checkbox + number input. Returns the value in use (same units as `fetched`)."""
    missing = fetched is None or (isinstance(fetched, float) and np.isnan(fetched))
    on = st.checkbox(f"Override {label}", value=missing, key=f"ov_on_{key}", help=help_txt)
    shown = default_if_missing if missing else float(fetched)
    val = st.number_input(f"{label}", value=float(shown), step=step, format=fmt,
                          key=f"ov_val_{key}_{shown}", disabled=not on, label_visibility="collapsed")
    if missing and not on:
        st.caption("⚠️ no fetched value; switch the override on")
    return (val if on else (np.nan if missing else float(fetched))), on


with st.sidebar:
    st.header("Manual overrides")
    st.caption("Tick a box to replace the fetched value. Unticked = live data.")
    beta_u, ov_beta = override("Unlevered beta", "beta", beta_fetched, 1.0, step=0.01)
    tax_us_pct, ov_tax_us = override("U.S. corporate tax rate (%)", "taxus", tax_us_fetched, 25.0, step=0.1)
    tax_fc_pct, ov_tax_fc = override(f"{country} corporate tax rate (%)", "taxfc",
                                     tax_us_pct if country == US else tax_fc_fetched, 25.0, step=0.1)
    s_usd_pct, ov_susd = override("USD BBB spread (%)", "susd", s_usd_fetched, 1.11, step=0.01)
    y_us_pct, ov_yus = override("U.S. 10Y government yield (%)", "yus", y_us_fetched, 4.0, step=0.01, fmt="%.3f")
    y_fc_pct, ov_yfc = override(f"{country} 10Y government yield (%)", "yfc",
                                y_us_pct if country == US and not np.isnan(y_us_pct) else y_fc_fetched,
                                4.0, step=0.01, fmt="%.3f")

if country == US and not ov_yfc:
    y_fc_pct = y_us_pct
if country == US and not ov_tax_fc:
    tax_fc_pct = tax_us_pct

tax_proj_pct = tax_us_pct if tax_choice.startswith("U.S.") else tax_fc_pct

# preliminary auto spread (needed to show it in the sidebar)
inputs_ok = not any(np.isnan(v) for v in [beta_u, tax_proj_pct, s_usd_pct, y_us_pct, y_fc_pct])
if inputs_ok:
    _pre = compute(beta_u, tax_proj_pct / 100, debt_pct / 100, erp_pct / 100, y_us_pct / 100,
                   y_fc_pct / 100, s_usd_pct / 100, None, method)
    s_fc_auto_pct = _pre["s_fc_auto"] * 100
else:
    s_fc_auto_pct = np.nan

with st.sidebar:
    fc_ccy = CURRENCY.get(country, "local currency")
    ov_sfc = st.toggle(f"Override {fc_ccy} BBB spread", value=False, key="ov_on_sfc")
    if ov_sfc:
        s_fc_pct = st.number_input(f"{fc_ccy} BBB spread (%)", value=float(round(s_fc_auto_pct, 4))
                                   if not np.isnan(s_fc_auto_pct) else 1.11, step=0.01, format="%.3f",
                                   key="ov_val_sfc")
    else:
        s_fc_pct = None
        st.caption(f"Auto-calculated {fc_ccy} BBB spread: **{s_fc_auto_pct:.3f}%**"
                   if not np.isnan(s_fc_auto_pct) else "Auto spread unavailable until inputs are complete")

if not inputs_ok:
    missing = [n for n, v in [("Unlevered beta", beta_u),
                              (("U.S." if tax_choice.startswith("U.S.") else country) + " corporate tax rate", tax_proj_pct),
                              ("USD BBB spread", s_usd_pct), ("U.S. 10Y yield", y_us_pct),
                              (f"{country} 10Y yield", y_fc_pct)] if np.isnan(v)]
    st.error("No fetched value for: **" + ", ".join(missing) + "**. Switch on the matching manual "
             "override in the sidebar and enter a value (see the Data sources tab for fetch errors).")
    st.stop()

res = compute(beta_u, tax_proj_pct / 100, debt_pct / 100, erp_pct / 100, y_us_pct / 100, y_fc_pct / 100,
              s_usd_pct / 100, None if s_fc_pct is None else s_fc_pct / 100, method)

# ----------------------------------------------------------------------------
# Status banner
# ----------------------------------------------------------------------------
problems = []
if err_beta:
    problems.append("Damodaran betas")
if err_bbb:
    problems.append("BBB spread (using Jan-2026 snapshot)")
if err_tax:
    problems.append("OECD tax rates (using snapshot)")
if err_yov:
    problems.append("Trading Economics overview (using per-country pages)")
if y_us_err or y_fc_err:
    problems.append("bond yield for " + ", ".join(dict.fromkeys(c for c, e in [(US, y_us_err), (country, y_fc_err)] if e)))
if problems:
    st.warning("Live fetch issue: " + "; ".join(problems) + ". Details in the **Data sources** tab; "
               "manual overrides are available in the sidebar.")

ov_list = [n for n, f in [("unlevered beta", ov_beta), ("U.S. tax", ov_tax_us), (f"{country} tax", ov_tax_fc),
                          ("USD BBB spread", ov_susd), ("U.S. yield", ov_yus), (f"{country} yield", ov_yfc),
                          (f"{fc_ccy} spread", ov_sfc)] if f]
if ov_list:
    st.info("Manual overrides in use: " + ", ".join(ov_list))

# ----------------------------------------------------------------------------
# Tabs
# ----------------------------------------------------------------------------
tab_res, tab_all, tab_assume, tab_check, tab_src = st.tabs(
    ["📊 Results", "🏭 All-industry comparison", "📘 Assumptions & formulas", "✅ Verification checks",
     "🔗 Data sources"])

# ---------------- Results ----------------
with tab_res:
    st.subheader(f"{industry}  |  USD vs {fc_ccy} ({country})")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("#### USD")
        m1, m2, m3 = st.columns(3)
        m1.metric("Cost of equity", pct(res["re_usd"]))
        m2.metric("Cost of debt (after tax)", pct(res["rd_usd_at"]))
        m3.metric("WACC", pct(res["wacc_usd"]))
    with c2:
        st.markdown(f"#### {fc_ccy} ({country})")
        m1, m2, m3 = st.columns(3)
        m1.metric("Cost of equity", pct(res["re_fc"]), delta=f"{(res['re_fc'] - res['re_usd']) * 100:+.2f} pp vs USD",
                  delta_color="off")
        m2.metric("Cost of debt (after tax)", pct(res["rd_fc_at"]),
                  delta=f"{(res['rd_fc_at'] - res['rd_usd_at']) * 100:+.2f} pp vs USD", delta_color="off")
        m3.metric("WACC", pct(res["wacc_fc"]), delta=f"{(res['wacc_fc'] - res['wacc_usd']) * 100:+.2f} pp vs USD",
                  delta_color="off")

    table = pd.DataFrame([
        ["10-year government bond yield", pct(res["y_us"], 3), pct(res["y_fc"], 3)],
        ["Unlevered beta (βU)", f"{res['beta_u']:.4f}", f"{res['beta_u']:.4f}"],
        ["Debt-to-equity D/E", f"{res['de']:.4f}", f"{res['de']:.4f}"],
        ["Project tax rate", pct(res["tax"]), pct(res["tax"])],
        ["Levered beta (βL)", f"{res['beta_l']:.4f}", f"{res['beta_l']:.4f}"],
        ["Equity risk premium", pct(res["erp"]), pct(res["erp"])],
        ["Cost of equity", pct(res["re_usd"], 3), pct(res["re_fc"], 3)],
        ["BBB spread", pct(res["s_usd"], 3),
         pct(res["s_fc"], 3) + (" (manual)" if s_fc_pct is not None else " (auto)")],
        ["Pre-tax cost of debt", pct(res["rd_usd"], 3), pct(res["rd_fc"], 3)],
        ["After-tax cost of debt", pct(res["rd_usd_at"], 3), pct(res["rd_fc_at"], 3)],
        ["Equity weight E/V", pct(res["e"], 1), pct(res["e"], 1)],
        ["Debt weight D/V", pct(res["d"], 1), pct(res["d"], 1)],
        ["WACC", pct(res["wacc_usd"], 3), pct(res["wacc_fc"], 3)],
    ], columns=["Item", "USD", f"{fc_ccy} ({country})"])
    st.dataframe(table, hide_index=True, width="stretch")

    fig = go.Figure()
    cats = ["Cost of equity", "After-tax cost of debt", "WACC"]
    fig.add_bar(name="USD", x=cats, y=[res["re_usd"] * 100, res["rd_usd_at"] * 100, res["wacc_usd"] * 100],
                text=[f"{v:.2f}%" for v in [res["re_usd"] * 100, res["rd_usd_at"] * 100, res["wacc_usd"] * 100]])
    fig.add_bar(name=fc_ccy, x=cats, y=[res["re_fc"] * 100, res["rd_fc_at"] * 100, res["wacc_fc"] * 100],
                text=[f"{v:.2f}%" for v in [res["re_fc"] * 100, res["rd_fc_at"] * 100, res["wacc_fc"] * 100]])
    fig.update_layout(barmode="group", yaxis_title="%", height=380, margin=dict(t=30, b=10))
    st.plotly_chart(fig, width="stretch")

    inputs_df = pd.DataFrame([
        ["Industry", industry, "Damodaran betas.xls" + (" (manual βU)" if ov_beta else "")],
        ["Selected country", country, ""],
        ["Project tax rate basis", tax_choice, ""],
        ["Conversion method", method, ""],
        ["Unlevered beta", beta_u, "manual" if ov_beta else "fetched"],
        ["U.S. tax rate (%)", tax_us_pct, "manual" if ov_tax_us else "fetched"],
        [f"{country} tax rate (%)", tax_fc_pct, "manual" if ov_tax_fc else "fetched"],
        ["Project tax rate (%)", tax_proj_pct, ""],
        ["Equity risk premium (%)", erp_pct, "user"],
        ["Debt share (%)", debt_pct, "user"],
        ["USD BBB spread (%)", s_usd_pct, "manual" if ov_susd else "fetched"],
        ["U.S. 10Y yield (%)", y_us_pct, "manual" if ov_yus else "fetched"],
        [f"{country} 10Y yield (%)", y_fc_pct, "manual" if ov_yfc else "fetched"],
        [f"{fc_ccy} BBB spread (%)", res["s_fc"] * 100, "manual" if s_fc_pct is not None else "auto"],
    ], columns=["Input", "Value", "Origin"])
    results_csv = pd.concat([
        inputs_df.rename(columns={"Input": "Item", "Value": "USD / value", "Origin": "Foreign / note"}),
        table.rename(columns={"USD": "USD / value", f"{fc_ccy} ({country})": "Foreign / note"}),
    ])
    st.download_button("⬇️ Download these results (CSV)", results_csv.to_csv(index=False).encode(),
                       file_name=f"cost_of_capital_{country.replace(' ', '_')}.csv", mime="text/csv")

# ---------------- All industries ----------------
with tab_all:
    if betas is None:
        st.warning("Industry betas could not be fetched, so the all-industry table is unavailable.")
        all_df = pd.DataFrame()
    else:
        rows = []
        for _, r in betas["df"].iterrows():
            bu = float(r["Unlevered beta"])
            if ov_beta and r["Industry"] == industry:
                bu = beta_u
            x = compute(bu, tax_proj_pct / 100, debt_pct / 100, erp_pct / 100, y_us_pct / 100, y_fc_pct / 100,
                        s_usd_pct / 100, None if s_fc_pct is None else s_fc_pct / 100, method)
            rows.append({"Industry": r["Industry"], "Unlevered beta": bu, "Levered beta": x["beta_l"],
                         "Cost of equity USD (%)": x["re_usd"] * 100, f"Cost of equity {fc_ccy} (%)": x["re_fc"] * 100,
                         "WACC USD (%)": x["wacc_usd"] * 100, f"WACC {fc_ccy} (%)": x["wacc_fc"] * 100})
        all_df = pd.DataFrame(rows).sort_values("WACC USD (%)", ascending=False).reset_index(drop=True)
        st.caption(f"Same settings for every industry: {country}, tax {tax_proj_pct:.2f}%, ERP {erp_pct:.2f}%, "
                   f"debt {debt_pct}%. Only the unlevered beta changes. Your industry is highlighted.")

        def hl(row):
            return ["background-color: #fff3b0" if row["Industry"] == industry else "" for _ in row]

        st.dataframe(all_df.style.apply(hl, axis=1).format(precision=3), width="stretch", height=420)
        show = st.radio("Chart", ["Top 15 + bottom 15 by WACC", "All industries"], horizontal=True)
        plot_df = all_df if show == "All industries" else pd.concat([all_df.head(15), all_df.tail(15)])
        plot_df = plot_df.iloc[::-1]
        fig2 = go.Figure()
        fig2.add_bar(name="WACC USD", y=plot_df["Industry"], x=plot_df["WACC USD (%)"], orientation="h")
        fig2.add_bar(name=f"WACC {fc_ccy}", y=plot_df["Industry"], x=plot_df[f"WACC {fc_ccy} (%)"], orientation="h")
        fig2.update_layout(barmode="group", height=max(500, 22 * len(plot_df)), xaxis_title="WACC (%)",
                           margin=dict(t=20, b=10, l=10))
        st.plotly_chart(fig2, width="stretch")
        st.download_button("⬇️ Download all-industry comparison (CSV)", all_df.to_csv(index=False).encode(),
                           file_name=f"all_industries_{country.replace(' ', '_')}.csv", mime="text/csv")

# ---------------- Assumptions ----------------
with tab_assume:
    st.markdown(rf"""
**Formulas** (all rates as decimals; *d* = target debt share, *t* = project tax rate)

1. Relever the industry beta (Hamada):  $\beta_L = \beta_U\,[1 + (1-t)\,\tfrac{{d}}{{1-d}}]$
2. USD cost of equity (CAPM):  $r_E^{{USD}} = y_{{US}} + \beta_L \times ERP$
3. USD pre-tax cost of debt:  $r_D^{{USD}} = y_{{US}} + s_{{BBB}}^{{USD}}$
4. Foreign-currency cost of equity, converted with the 10-year yield differential (parity):
   - Multiplicative: $1 + r^{{FC}} = (1 + r^{{USD}})\,\dfrac{{1 + y_{{FC}}}}{{1 + y_{{US}}}}$
   - Additive approximation: $r^{{FC}} = r^{{USD}} + (y_{{FC}} - y_{{US}})$
5. Foreign-currency BBB spread (auto): convert $r_D^{{USD}}$ the same way and subtract $y_{{FC}}$, so
   $r_D^{{FC}} = y_{{FC}} + s^{{FC}}$. With an override, $r_D^{{FC}} = y_{{FC}} + s^{{FC}}_{{manual}}$.
6. WACC (both currencies):  $WACC = (1-d)\,r_E + d\,r_D\,(1-t)$

**Main assumptions**
- Industry risk is measured by Damodaran's U.S. **unlevered beta** (not the cash-corrected one) and relevered at the project's target debt share.
- The **same** project tax rate, debt share, beta and ERP are used in both currencies; you choose whether the tax rate is the U.S. or the selected country's statutory combined (central + sub-central) rate from the OECD.
- The 10-year government yields are treated as the risk-free rates. Converting USD rates with the yield differential assumes the difference in yields reflects expected inflation / currency depreciation (international Fisher / interest-rate parity) and that the government bond is default-free. Emerging-market yields can include sovereign default risk, which this approach passes through to the project.
- The USD BBB spread is Damodaran's Baa2/BBB spread for large non-financial service firms. Unless overridden, the foreign spread is the parity-consistent conversion of the USD cost of debt.
- Equity risk premium is a USD global premium chosen by the user (default 6.5%). No separate country risk premium is added.
- Market-value weights equal the target capital structure; debt is assumed permanent and the interest tax shield is fully usable.
""")

# ---------------- Verification ----------------
with tab_check:
    st.markdown("Automated checks using **your current inputs**:")
    base = dict(beta_u=beta_u, tax=tax_proj_pct / 100, d=debt_pct / 100, erp=erp_pct / 100,
                y_us=y_us_pct / 100, s_usd=s_usd_pct / 100, method=method)
    # 1) equal yields
    t1 = compute(**base, y_fc=y_us_pct / 100, s_fc_override=None)
    ok1 = all(abs(t1[a] - t1[b]) < 1e-12 for a, b in [("re_usd", "re_fc"), ("rd_usd", "rd_fc"), ("wacc_usd", "wacc_fc")])
    # 2) zero debt
    t2 = compute(**{**base, "d": 0.0}, y_fc=y_fc_pct / 100, s_fc_override=None)
    ok2 = abs(t2["wacc_usd"] - t2["re_usd"]) < 1e-12 and abs(t2["wacc_fc"] - t2["re_fc"]) < 1e-12
    # 3) foreign spread override leaves USD unchanged
    t3a = compute(**base, y_fc=y_fc_pct / 100, s_fc_override=None)
    t3b = compute(**base, y_fc=y_fc_pct / 100, s_fc_override=0.05)
    ok3 = all(abs(t3a[k] - t3b[k]) < 1e-15 for k in ["re_usd", "rd_usd", "wacc_usd"]) and t3a["wacc_fc"] != t3b["wacc_fc"]
    checks = pd.DataFrame([
        ["Equal government yields → equal results (spread override off)", "PASS ✅" if ok1 else "FAIL ❌",
         f"WACC USD {pct(t1['wacc_usd'], 4)} vs FC {pct(t1['wacc_fc'], 4)}"],
        ["Zero debt → WACC equals cost of equity", "PASS ✅" if ok2 else "FAIL ❌",
         f"USD: {pct(t2['wacc_usd'], 4)} = {pct(t2['re_usd'], 4)}; FC: {pct(t2['wacc_fc'], 4)} = {pct(t2['re_fc'], 4)}"],
        ["Overriding foreign spread (to 5%) leaves USD results unchanged", "PASS ✅" if ok3 else "FAIL ❌",
         f"WACC USD {pct(t3a['wacc_usd'], 4)} → {pct(t3b['wacc_usd'], 4)}; WACC FC {pct(t3a['wacc_fc'], 4)} → {pct(t3b['wacc_fc'], 4)}"],
    ], columns=["Check", "Result", "Evidence"])
    st.dataframe(checks, hide_index=True, width="stretch")

    st.markdown("#### Worked example (copy into a spreadsheet to verify by hand)")
    r = res
    conv = "(1 + r_USD) × (1 + y_FC) / (1 + y_US) − 1" if method.startswith("Multi") else "r_USD + (y_FC − y_US)"
    st.code(
        f"""Inputs:  βU = {r['beta_u']:.4f}   t = {r['tax']:.4%}   d = {r['d']:.2%}   ERP = {r['erp']:.2%}
         y_US = {r['y_us']:.4%}   y_FC = {r['y_fc']:.4%}   s_USD = {r['s_usd']:.4%}
D/E       = d/(1−d)                         = {r['de']:.6f}
βL        = βU × (1 + (1−t) × D/E)          = {r['beta_l']:.6f}
rE (USD)  = y_US + βL × ERP                 = {r['re_usd']:.6%}
rD (USD)  = y_US + s_USD                    = {r['rd_usd']:.6%}
WACC USD  = (1−d)×rE + d×rD×(1−t)           = {r['wacc_usd']:.6%}
Conversion: {conv}
rE (FC)   = {r['re_fc']:.6%}
s_FC      = {r['s_fc']:.6%}  ({'manual' if s_fc_pct is not None else 'auto'})
rD (FC)   = y_FC + s_FC                     = {r['rd_fc']:.6%}
WACC FC   = (1−d)×rE + d×rD×(1−t)           = {r['wacc_fc']:.6%}""", language="text")

# ---------------- Sources ----------------
with tab_src:
    src = pd.DataFrame([
        ["Industry unlevered beta", "Aswath Damodaran (NYU Stern)", betas["source"] if betas else BETA_URL,
         betas["date"] if betas else "—", betas["fetched"] if betas else "—",
         "OK" if betas else f"FAILED: {err_beta}"],
        ["USD BBB (Baa2) spread, large non-financial firms", "Aswath Damodaran (NYU Stern)", RATINGS_URL,
         bbb["date"] if bbb else BBB_FALLBACK[1], bbb["fetched"] if bbb else "—",
         f"OK ({s_usd_fetched:.2f}%)" if bbb else f"FALLBACK: {err_bbb}"],
        ["Corporate tax rates (combined statutory)", "OECD Corporate Tax Statistics", OECD_PAGE_URL,
         tax_src["date"], tax_src["fetched"], "OK" if tax else f"FALLBACK: {err_tax}"],
        ["U.S. 10Y government yield", "Trading Economics", y_us_src, y_us_date or "—",
         yov["fetched"] if yov else now_str(), "OK" if not y_us_err else f"FAILED: {y_us_err}"],
        [f"{country} 10Y government yield", "Trading Economics", y_fc_src, y_fc_date or "—",
         yov["fetched"] if yov else now_str(), "OK" if not y_fc_err else f"FAILED: {y_fc_err}"],
    ], columns=["Data", "Provider", "URL", "Data date", "Fetched", "Status"])
    st.dataframe(src, hide_index=True, width="stretch",
                 column_config={"URL": st.column_config.LinkColumn("URL")})
    st.markdown(f"""
- **Tax rate used for {country}:** {tax_fc_pct:.2f}% (OECD year {tax_fc_year if tax_fc_year is not None else 'n/a'})
  · **U.S.:** {tax_us_pct:.2f}% (OECD year {tax_us_year if tax_us_year is not None else 'n/a'})
- **Unlevered beta for {industry}:** {beta_fetched if not np.isnan(beta_fetched) else 'n/a'} (fetched)
- Data are cached for 6 hours. Use **Refresh data fetch** in the sidebar to download again.
""")
    if tax is not None:
        st.caption(f"OECD series used: {tax.get('series', '')}")
    with st.expander("All fetched bond yields"):
        st.dataframe(yields_df.drop(columns=["slug"]), hide_index=True, width="stretch")
    with st.expander("All fetched tax rates"):
        st.dataframe(tax_df, hide_index=True, width="stretch")
    if betas is not None:
        with st.expander("All fetched industry betas"):
            st.dataframe(betas["df"], hide_index=True, width="stretch")

    # Excel download of everything
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        inputs_df.to_excel(xw, sheet_name="Inputs", index=False)
        table.to_excel(xw, sheet_name="Results", index=False)
        if not all_df.empty:
            all_df.to_excel(xw, sheet_name="All industries", index=False)
        src.to_excel(xw, sheet_name="Sources", index=False)
    st.download_button("⬇️ Download full workbook (Excel)", buf.getvalue(),
                       file_name=f"cost_of_capital_{country.replace(' ', '_')}.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

st.caption("Built for Kellogg International Finance. Formulas are documented in the Assumptions tab; "
           "verify against the class formulas before relying on results.")
