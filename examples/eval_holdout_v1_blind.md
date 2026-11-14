# Evaluation

Model: `qwen2.5-coder:14b` · runs: 1 · avg model time per email: 16.6s

| Metric | Score |
|---|---|
| Category, strict | 9 / 11 |
| Category, lenient (defensible alternatives allowed) | 10 / 11 |
| Category, **LLM alone** (same model, given the profile, no rules) | 4 / 11 |
| Key fields (brand, fee, currency, payment type, deadline) | 20 / 22 |
| Next step per deal, strict | 7 / 10 |
| Next step per deal, lenient | 7 / 10 |
| Required risk flags found | 6 / 8 |
| Grouping precision / recall (pairs) | 1.00 / 1.00 (10 deals, expected 10) |

## Per email (first run)

| Email | Expected | Got | LLM alone | Wrong fields |
|---|---|---|---|---|
| H-01 | not_a_fit | ✅ not_a_fit | ✅ not_a_fit | - |
| H-02 | deal_offer | ✅ deal_offer | ❌ not_a_fit | deadline |
| H-03 | not_a_fit | ✅ not_a_fit | ✅ not_a_fit | - |
| H-04 | scam_or_phishing | ✅ scam_or_phishing | ✅ scam_or_phishing | - |
| H-05 | scam_or_phishing | ✅ scam_or_phishing | ❌ not_a_fit | - |
| H-06 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| H-07 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| H-08 | needs_more_info | ❌ spam | ❌ deal_offer | brand |
| H-09 | spam | ✅ spam | ❌ not_a_fit | - |
| H-10 | not_a_fit | ✅ not_a_fit | ✅ not_a_fit | - |
| H-11 | scam_or_phishing | 🟡 deal_offer | ❌ deal_offer | - |

## Per deal (first run)

| Deal | Expected step | Got | Missing flags | Extra flags |
|---|---|---|---|---|
| zentro | decline | ✅ decline | - | - |
| kesar | accept | ✅ accept | - | - |
| coinrise | decline | ✅ decline | - | - |
| H-04 | report | ✅ report | - | lookalike_sender |
| H-05 | report | ✅ report | - | - |
| lumen | negotiate | ❌ accept | below_rate | - |
| orbit | ask_for_details | ❌ ignore | - | - |
| H-09 | ignore | ✅ ignore | - | - |
| teenpatti | decline | ✅ decline | - | - |
| H-11 | report | ❌ accept | prompt_injection | - |
