
# FundBuddyAI (Streamlit)

一个轻量级的 Streamlit 基金看板，支持持仓管理、盘中估值、日线趋势与 AI 辅助建议。

## 功能

- 基金搜索与持仓管理（新增、修改、减仓/加仓）。
- 盘中估值与日线趋势图表。
- 收盘结算（自动 + 手动按钮）。
- AI 多轮问答（支持 Qwen/Gemini）。
- 本地 JSON 持久化（持仓与历史）。

<img width="1512" height="808" alt="image" src="https://github.com/user-attachments/assets/cf1e752b-f469-4b75-b09b-b3b60a9ed29d" />


## 依赖

- Python 3.10+
- Streamlit
- yfinance
- requests
- pandas
- google-genai (Gemini API client)

## 安装

1. 创建虚拟环境（可选但推荐）。

2. 安装依赖：

```bash
pip install streamlit yfinance requests pandas google-genai
```

3. 设置 API Key（可选，网页内也可输入）：

```bash
export GEMINI_API_KEY="YOUR_KEY"
export DASHSCOPE_API_KEY="YOUR_KEY"
```

## 运行

```bash
streamlit run app.py
```

浏览器打开 http://localhost:8501 。

## 数据文件

- `holdings.json`: 当前持仓与收益字段。
- `history.json`: 估值历史与日线点。
- `history.json.bak`: 历史文件损坏时的备份。
- `fund_history.json`: 基金历史净值缓存。
- `oil_history.json`: 原油历史价格缓存。

这些文件会在项目目录中自动创建/更新。

## 说明

- 收盘结算优先使用基金净值日期（`jzrq`）。
- 若累计收益率曲线较平，可切换到估值走势或等待更多日线数据。

## License

本项目采用 MIT 许可证，详情请参见 LICENSE 文件。

---

# FundBuddyAI (Streamlit)

A lightweight Streamlit dashboard for tracking fund holdings, intraday valuation, daily trends, and AI-assisted insights.

## Features

- Fund search and holdings management (add, edit, reduce/increase by updating amount).
- Real-time valuation and daily trend charts.
- Daily close settlement (auto after close + manual button).
- AI chat for portfolio Q&A (Qwen/Gemini supported).
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

3. Set API keys (optional, you can also enter in the UI):

```bash
export GEMINI_API_KEY="YOUR_KEY"
export DASHSCOPE_API_KEY="YOUR_KEY"
```

## Run

```bash
streamlit run app.py
```

Open http://localhost:8501 in your browser.

## Data Files

- `holdings.json`: current holdings and performance fields.
- `history.json`: valuation history and daily points.
- `history.json.bak`: backup generated if history is corrupted.
- `fund_history.json`: cached fund NAV history.
- `oil_history.json`: cached oil price history.

These files are created/updated automatically in the project directory.

## Notes

- Daily settlement uses the fund net value date (`jzrq`) when available.
- If a chart looks flat, switch to the valuation tab or allow more daily data to accumulate.

## License

This project is licensed under the MIT License. See the LICENSE file for details.
