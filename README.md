
# Fund Watcher (Streamlit)

A lightweight Streamlit dashboard for tracking fund holdings, intraday valuation, and daily trends with AI-assisted insights.

## Features

- Fund search and holdings management (add, edit, reduce/increase by updating amount).
- Real-time valuation and daily trend charts.
- Daily close settlement (auto after close + manual button).
- AI chat for portfolio Q&A (Gemini API).
- Local JSON storage for holdings and history.

## Screenshots

Add your screenshots here if you want.

## Requirements

- Python 3.10+
- Streamlit
- yfinance
- requests
- pandas
- google-genai (Gemini API client)

## Setup

1. Create a virtual environment (optional but recommended).

2. Install dependencies:

```bash
pip install streamlit yfinance requests pandas google-genai
```

3. Set your Gemini API key:

```bash
export GEMINI_API_KEY="YOUR_KEY"
```

## Run

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

## Data Files

- `holdings.json`: current holdings and performance fields.
- `history.json`: valuation history and daily points for charts.
- `history.json.bak`: backup generated if history is corrupted.

These files are created/updated automatically in the project directory.

## Notes

- Daily settlement uses the fund net value date (`jzrq`) when available.
- If a chart looks flat, switch to the valuation tab or allow more daily data to accumulate.

## License

Choose a license and add it here (MIT is a common choice for open-source).
