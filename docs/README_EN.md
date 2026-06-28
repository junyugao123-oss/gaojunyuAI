# Meiri Guyan AI

**Real-time AI quantitative evaluation for A-shares and Hong Kong stocks.**

Meiri Guyan AI is a focused stock analysis product. A user enters one stock name or code, and the system combines market data, valuation, sectors, news, financials, and quantitative signals into a concise analysis page.

The current MVP focuses on A/H stocks, mobile-first readability, real-time single-stock analysis, dynamic valuation ranges, a six-dimensional health score, and an actionable price plan.

## Product Focus

The product is not a news portal and not a generic chatbot. Its job is to answer practical questions:

- Is the current price attractive or expensive?
- Where is the dynamic valuation range?
- Is the growth story supported by industry, moat, and execution evidence?
- Are related sectors moving with or against the stock?
- Are recent news items positive, negative, or neutral?
- What are the watch, confirmation, and invalidation levels?

## Core Modules

| Module | Description |
| --- | --- |
| Landing search | A/H stock search by name, code, and aliases |
| Real-time analysis page | Single-stock conclusion, valuation, scores, sectors, news, and point plan |
| Six-dimensional health score | Value, valuation cost-effectiveness, growth, profitability, financial strength, dividend |
| Dynamic valuation range | Generated from price, history, volatility, fundamentals, and risk factors |
| Point plan | Watch zone, confirmation level, and invalidation level |
| Related sectors | Relevance plus real-time sector movement |
| Curated news | Selected latest news with positive/negative/neutral labels |
| Industry trend | Quantified industry direction for long-term growth assessment |

## Scoring Philosophy

The score should be explainable and realistic. Growth is not raised just because a stock is popular. The system looks for a chain of evidence:

```text
business -> industry track -> supply-chain position -> moat -> execution evidence
```

ST stocks, persistent losses, weak cash flow, audit risk, delisting risk, and financial deterioration reduce relevant scores.

## Architecture

```text
apps/dsa-web    React/Vite web frontend
api             FastAPI endpoints
src             analysis, configuration, and service layer
tests           regression and score calibration tests
static          production frontend assets
docs            project documentation
```

## Local Development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cd apps/dsa-web
npm install
cd ../..
```

Start backend:

```bash
PYTHONPATH=. ./.venv/bin/python -m uvicorn server:app --host 0.0.0.0 --port 8000
```

Start frontend:

```bash
npm --prefix apps/dsa-web run dev -- --host 0.0.0.0
```

Useful URLs:

- Web: `http://localhost:5173`
- API: `http://localhost:8000`
- Example endpoint: `http://localhost:8000/api/v1/commercial-analysis/HK6651`

## Build And Test

```bash
npm --prefix apps/dsa-web run build
PYTHONPATH=. ./.venv/bin/python -m compileall api tests
```

Commercial score regression:

```bash
PYTHONPATH=. ./.venv/bin/python - <<'PY'
from tests import test_commercial_score_calibration as t
for name in sorted(n for n in dir(t) if n.startswith("test_")):
    getattr(t, name)()
    print(f"{name}: ok")
PY
```

## Deployment Notes

For mainland China cloud deployment, prefer domestic-accessible data and model providers. Keep real API keys in environment variables, never in Git history.

## Disclaimer

This MVP is for research and product demonstration. It does not provide investment advice.
