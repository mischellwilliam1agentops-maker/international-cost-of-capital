# International Cost of Capital App

A Streamlit app that compares a project's **cost of equity, cost of debt and WACC in USD and in the currency of any selected country**, using live market and tax data.

**Live app:** _add your streamlit.app link here_

## What it does

- Fetches the latest data, shows each source with its data date and fetch time, and has a **Refresh data fetch** button.
- Lets you pick an **industry** (Damodaran U.S. industries) and a **target country**, and set the **equity risk premium**, **target debt share**, and whether the project uses the **U.S. or the selected country's tax rate**.
- Lets you **override** any fetched number: unlevered beta, U.S. and foreign tax rates, USD BBB spread, U.S. and foreign 10Y yields. The **foreign BBB spread** has its own toggle: when it is off, the app shows the auto-calculated spread.
- Shows USD and foreign-currency results **side by side**, explains the formulas and assumptions, runs the required **verification checks** automatically, and compares **all industries** under the same settings.
- Downloads: results (CSV), all-industry comparison (CSV), and a full Excel workbook (inputs, results, all industries, sources).

**Defaults on opening:** Software (System & Application), United States as the selected country, U.S. tax rate, 6.5% equity risk premium, 30% debt.

## Data sources

| Input | Source | Notes |
|---|---|---|
| Industry unlevered beta | Aswath Damodaran, [betas.xls](https://pages.stern.nyu.edu/~adamodar/pc/datasets/betas.xls) | "Unlevered beta" column (not the cash-corrected one) |
| USD BBB spread | Damodaran, [ratings.html](https://pages.stern.nyu.edu/adamodar/New_Home_Page/datafile/ratings.html) | Baa2/BBB row for large non-financial service firms |
| Corporate tax rates | [OECD Corporate Tax Statistics](https://www.oecd.org/en/publications/corporate-tax-statistics-2024_9c27d6e8-en/full-report/statutory-corporate-income-tax-rates_440f9286.html) | Combined (central + sub-central) statutory rate, latest year, read from the OECD SDMX database behind the report |
| 10-year government bond yields | [Trading Economics](https://tradingeconomics.com/bonds) | Scraped from the bonds page (no API key); falls back to each country's own page |

Data are cached for 6 hours. If a source cannot be reached, the app says so, shows the error in the **Data sources** tab, and asks for a manual override (the BBB spread and some tax rates have a dated fallback snapshot).

## Formulas

All rates are decimals. *d* = target debt share D/(D+E), *t* = project tax rate.

1. Relevered beta: βL = βU × [1 + (1 − t) × d/(1 − d)]
2. USD cost of equity: rE(USD) = y(US) + βL × ERP
3. USD cost of debt: rD(USD) = y(US) + BBB spread(USD)
4. Conversion to the foreign currency using the 10-year yield differential:
   - Multiplicative (default): 1 + r(FC) = (1 + r(USD)) × (1 + y(FC)) / (1 + y(US))
   - Additive (option): r(FC) = r(USD) + y(FC) − y(US)
5. Foreign BBB spread (auto) = converted rD(USD) − y(FC), so rD(FC) = y(FC) + spread(FC). With the override on, the manual spread is used instead.
6. WACC = (1 − d) × rE + d × rD × (1 − t), in each currency, with the same t, d, ERP and beta.

## Key assumptions

- Industry risk comes from Damodaran's U.S. unlevered beta, relevered at the target debt share.
- 10-year government yields are the risk-free rates; their difference reflects expected inflation / currency depreciation (international Fisher effect / parity). Any sovereign default risk in the foreign yield is passed through.
- One ERP (default 6.5%) for both currencies; no separate country risk premium.
- Target capital structure = market-value weights; full use of the interest tax shield.

## Verification

The **Verification checks** tab tests, with the current inputs:
1. Equal government yields give equal USD and foreign results (foreign spread override off).
2. Zero debt makes WACC equal the cost of equity.
3. Overriding the foreign spread leaves all USD results unchanged.

It also prints a step-by-step worked example to check by hand in a spreadsheet.

## Run or deploy

**Streamlit Community Cloud:** put `app.py`, `requirements.txt` and `README.md` at the top level of a public GitHub repo, then at share.streamlit.io choose Create app → select the repo, branch `main`, file `app.py`, Python 3.12 → Deploy.

**Locally:**
```
pip install -r requirements.txt
streamlit run app.py
```

_Built with help from Claude (Anthropic), November 2026. Calculations verified independently in a spreadsheet._
