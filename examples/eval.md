# Evaluation

Model: `qwen2.5-coder:14b` · runs: 1 · avg model time per email: 0.0s

| Metric | Score |
|---|---|
| Category, strict | 15 / 15 |
| Category, lenient (defensible alternatives allowed) | 15 / 15 |
| Category, **LLM alone** (same model, given the profile, no rules) | 9 / 15 |
| Key fields (brand, fee, currency, payment type, deadline) | 39 / 40 |
| Next step per deal, strict | 13 / 13 |
| Next step per deal, lenient | 13 / 13 |
| Required risk flags found | 14 / 14 |
| Grouping precision / recall (pairs) | 1.00 / 1.00 (13 deals, expected 13) |

## Per email (first run)

| Email | Expected | Got | LLM alone | Wrong fields |
|---|---|---|---|---|
| E-01 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| E-02 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| E-03 | scam_or_phishing | ✅ scam_or_phishing | ✅ scam_or_phishing | fee_amount |
| E-04 | not_a_fit | ✅ not_a_fit | ✅ not_a_fit | - |
| E-05 | not_a_fit | ✅ not_a_fit | ✅ not_a_fit | - |
| E-06 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| E-07 | scam_or_phishing | ✅ scam_or_phishing | ✅ scam_or_phishing | - |
| E-08 | needs_more_info | ✅ needs_more_info | ✅ needs_more_info | - |
| E-09 | deal_offer | ✅ deal_offer | ✅ deal_offer | - |
| E-10 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| E-11 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| E-12 | deal_offer | ✅ deal_offer | ❌ not_a_fit | - |
| E-13 | spam | ✅ spam | ✅ spam | - |
| E-14 | scam_or_phishing | ✅ scam_or_phishing | ✅ scam_or_phishing | - |
| E-15 | needs_more_info | ✅ needs_more_info | ✅ needs_more_info | - |

## Per deal (first run)

| Deal | Expected step | Got | Missing flags | Extra flags |
|---|---|---|---|---|
| dewlab | negotiate | ✅ negotiate | - | - |
| E-03 | report | ✅ report | - | pressure_tactics |
| E-04 | decline | ✅ decline | - | - |
| E-05 | decline | ✅ decline | - | - |
| E-06 | negotiate | ✅ negotiate | - | personal_email_domain |
| E-07 | report | ✅ report | - | - |
| E-08 | ask_for_details | ✅ ask_for_details | - | - |
| E-09 | negotiate | ✅ negotiate | - | - |
| E-10 | negotiate | ✅ negotiate | - | fee_from_text |
| E-12 | ask_for_details | ✅ ask_for_details | - | - |
| E-13 | ignore | ✅ ignore | - | - |
| E-14 | report | ✅ report | - | pressure_tactics |
| E-15 | ask_for_details | ✅ ask_for_details | - | attachment_only |
