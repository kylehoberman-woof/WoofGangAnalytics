# Woof Gang Store Performance Analyzer

Pulls transaction data from FranPOS, generates Excel workbooks and interactive HTML dashboards for your stores.

## What You Get

- **Full Performance Analysis** (Excel) — Revenue breakdown, grooming vs retail, customer stats, employee productivity, daily/weekly/monthly trends
- **Interactive Dashboard** (HTML) — Visual charts, KPIs, revenue trends, staffing heatmaps, customer concentration analysis
- **Price Increase Analysis** (Excel + HTML) — Impact modeling for price changes on grooming/bath services

## Setup (One Time)

### 1. Install Python 3.12+

If you don't have Python:
- Go to https://www.python.org/downloads/ and install 3.12 or newer
- Or if you have Homebrew: `brew install python@3.12`

### 2. Install uv (Python package manager)

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

### 3. Install dependencies

```bash
cd store-analysis
uv sync
```

That's it — you're ready to run.

## Running the Analysis

### Port Washington (#264)

```bash
cd store-analysis
uv run python scripts/run.py
uv run python scripts/generate_dashboards.py
```

The outputs land in `port-washington/`:
- `WoofGang_PortWashington_2025_Analysis.xlsx`
- `WoofGang_PortWashington_2025_Dashboard.html`

### Hicksville (#265)

```bash
cd store-analysis
uv run python scripts/run_hicksville.py
```

The outputs land in `hicksville/`:
- `WoofGang_Hicksville_NY_2025-2026_Analysis.xlsx`
- `WoofGang_Hicksville_Dashboard.html`

### Price Increase Analysis

```bash
uv run python scripts/price_increase_analysis.py
```

### Changing the Date Range

Edit the dates at the top of `scripts/run.py` (Port Washington) or `scripts/run_hicksville.py` (Hicksville):

```python
START_DATE = "2025-01-01"
END_DATE = "2025-12-31"
```

Then re-run the scripts.

## Viewing Reports

- **Excel files** — Open in Excel, Google Sheets, or Numbers
- **HTML dashboards** — Double-click to open in your browser (Chrome/Safari/Firefox)

## File Structure

```
store-analysis/
├── scripts/
│   ├── run.py                      # Main pipeline (Port Washington config)
│   ├── run_hicksville.py           # Hicksville wrapper
│   ├── generate_dashboards.py      # HTML dashboard generator
│   └── price_increase_analysis.py  # Price increase modeling
├── port-washington/
│   ├── data/                       # Cached API data
│   └── *.xlsx, *.html              # Output reports
├── hicksville/
│   ├── data/                       # Cached API data
│   └── *.xlsx, *.html              # Output reports
└── pyproject.toml                  # Python dependencies
```

## Using with Claude

If you have Claude Code installed (`npm install -g @anthropic-ai/claude-code`), you can ask Claude to:

- "Run the Port Washington analysis for Q1 2026"
- "Update the date range to March 1-31 and re-run"
- "Generate a price increase analysis for Hicksville with a $7 increase"
- "Compare this month vs last month"

Just open a terminal in the `store-analysis` folder, type `claude`, and ask.

## Troubleshooting

**"ModuleNotFoundError"** — Run `uv sync` first to install dependencies.

**API timeout** — FranPOS can be slow. The script retries automatically. Just wait.

**Empty data** — Make sure the date range has actual transactions. Check `START_DATE` and `END_DATE`.
