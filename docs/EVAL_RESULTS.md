# Evaluation results

Run `20261022-100604` · 2026-10-22 10:21 UTC · live web data · models: scout/analyst/strategist `claude-opus-5-5`, page reading `claude-haiku-5-5`

## Launch Spy vs plain Claude

| Market | System | Found / expected | Recall | Sourced facts | Time | Cost |
|---|---|---|---|---|---|---|
| A dating app for young professionals in Paris | launch_spy | 6/8 | 75% | 157 | 75.5s | $0.120 |
| A dating app for young professionals in Paris | plain_claude | 8/8 | 100% | 0 | 2.9s | $0.002 |
| A CRM for small businesses | launch_spy | 5/9 | 56% | 131 | 117.6s | $0.126 |
| A CRM for small businesses | plain_claude | 6/9 | 67% | 0 | 6.1s | $0.002 |
| An AI meeting note taker app | launch_spy | 6/8 | 75% | 181 | 109.5s | $0.138 |
| An AI meeting note taker app | plain_claude | 7/8 | 88% | 0 | 4.0s | $0.002 |
| A language learning app | launch_spy | 5/8 | 62% | 79 | 159.0s | $0.108 |
| A language learning app | plain_claude | 6/8 | 75% | 0 | 2.8s | $0.002 |
| A project management tool for software teams | launch_spy | 6/9 | 67% | 151 | 72.7s | $0.141 |
| A project management tool for software teams | plain_claude | 8/9 | 89% | 0 | 2.8s | $0.002 |

**Average recall:** Launch Spy 67% vs plain Claude 84%.  
**Grounding:** 100% of Launch Spy's facts link to a stored page snapshot (enforced by the schema); plain Claude gives 0 sources.  
**Cost per market:** Launch Spy $0.13 · plain Claude $0.002.

## Memory curve (same market, 'Continue research')

| Run | Time | New LLM calls | New cost | Facts reused from memory |
|---|---|---|---|---|
| 1st hunt (fresh) | 75.5s | 17 | $0.120 | 72 |
| Continue research | 172.1s | 0 | $0.000 | 175 |

Answer key: `backend/scripts/data/answer_key.json`. Raw rows: table `eval_runs` in Aiven Postgres.
