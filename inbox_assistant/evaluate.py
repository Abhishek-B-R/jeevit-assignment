"""Score a run against eval/expected.json."""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from itertools import combinations

from .models import Deal, EmailResult

FIELDS = ["brand", "fee_amount", "currency", "payment_type", "deadline"]


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s).lower().replace("&", "and"))


def _field_ok(field: str, expected, result: EmailResult, deal: Deal) -> bool:
    # Score what the creator sees: the deal's merged terms for brand, the email's own read for the rest.
    ex = result.extraction
    if field == "brand":
        got = deal.terms.brand if result.category not in ("scam_or_phishing", "spam") else ex.brand
        if expected is None:
            return got is None
        return got is not None and (_norm(expected) in _norm(got) or _norm(got) in _norm(expected))
    got = getattr(ex, field)
    if field == "deadline":
        got = got.isoformat() if isinstance(got, date) else got
    if field == "payment_type" and got == "unknown":
        got = None
    if expected is None:
        return got is None
    if field == "fee_amount":
        return got is not None and abs(float(got) - float(expected)) < 1
    return got == expected


def score(expected: dict, deals: list[Deal], results: list[EmailResult], baseline: dict[str, str | None]) -> dict:
    exp_e, exp_d = expected["emails"], expected["deals"]
    by_id = {r.id: r for r in results}
    deal_of = {eid: d for d in deals for eid in d.email_ids}

    rows = []
    for eid, e in exp_e.items():
        r = by_id[eid]
        ok_strict = r.category == e["category"]
        ok_lenient = ok_strict or r.category in e.get("also_ok", [])
        fields = {f: _field_ok(f, e[f], r, deal_of[eid]) for f in FIELDS if f in e}
        rows.append({
            "id": eid, "expected": e["category"], "got": r.category, "strict": ok_strict, "lenient": ok_lenient,
            "llm_only": baseline.get(eid), "llm_only_ok": baseline.get(eid) == e["category"],
            "fields": fields,
        })

    # Grouping: pairwise precision / recall over "same deal" pairs.
    exp_pairs = {frozenset(p) for p in combinations(exp_e, 2) if exp_e[p[0]]["group"] == exp_e[p[1]]["group"]}
    got_pairs = {frozenset(p) for p in combinations(exp_e, 2) if deal_of[p[0]].deal_id == deal_of[p[1]].deal_id}
    tp = len(exp_pairs & got_pairs)
    grouping = {
        "precision": tp / len(got_pairs) if got_pairs else 1.0,
        "recall": tp / len(exp_pairs) if exp_pairs else 1.0,
        "expected_groups": len({e["group"] for e in exp_e.values()}),
        "got_groups": len({d.deal_id for d in deals}),
    }

    # Deal-level: action and required flags.
    first_email = defaultdict(list)
    for eid, e in exp_e.items():
        first_email[e["group"]].append(eid)
    deal_rows = []
    for g, spec in exp_d.items():
        d = deal_of[sorted(first_email[g])[-1]]
        codes = {f.code for f in d.flags}
        missing = []
        for req in spec["flags"]:
            options = req if isinstance(req, list) else [req]
            if not codes & set(options):
                missing.append("/".join(options))
        deal_rows.append({
            "group": g, "expected": spec["action"], "got": d.action,
            "strict": d.action == spec["action"],
            "lenient": d.action == spec["action"] or d.action in spec.get("also_ok", []),
            "missing_flags": missing,
            "extra_flags": sorted(codes - {c for req in spec["flags"] for c in (req if isinstance(req, list) else [req])}),
            "required_flags": len(spec["flags"]),
        })

    n = len(rows)
    field_total = sum(len(r["fields"]) for r in rows)
    field_ok = sum(sum(r["fields"].values()) for r in rows)
    flag_total = sum(r["required_flags"] for r in deal_rows)
    flag_hit = flag_total - sum(len(r["missing_flags"]) for r in deal_rows)
    return {
        "category_strict": sum(r["strict"] for r in rows),
        "category_lenient": sum(r["lenient"] for r in rows),
        "category_llm_only": sum(r["llm_only_ok"] for r in rows),
        "emails": n,
        "fields_ok": field_ok, "fields_total": field_total,
        "action_strict": sum(r["strict"] for r in deal_rows),
        "action_lenient": sum(r["lenient"] for r in deal_rows),
        "deals": len(deal_rows),
        "flags_hit": flag_hit, "flags_total": flag_total,
        "grouping": grouping,
        "rows": rows, "deal_rows": deal_rows,
    }


def render_eval(runs: list[dict], model: str, seconds_per_email: float) -> str:
    first = runs[0]
    k = len(runs)

    def stat(key, total_key=None):
        vals = [r[key] for r in runs]
        total = runs[0][total_key] if total_key else None
        lo, hi = min(vals), max(vals)
        mean = sum(vals) / k
        rng = f"{lo}" if lo == hi else f"{mean:.1f} (min {lo}, max {hi})"
        return f"{rng} / {total}" if total is not None else rng

    out = [f"# Evaluation", "", f"Model: `{model}` · runs: {k} · avg model time per email: {seconds_per_email:.1f}s", ""]
    out += ["| Metric | Score |", "|---|---|",
            f"| Category, strict | {stat('category_strict', 'emails')} |",
            f"| Category, lenient (defensible alternatives allowed) | {stat('category_lenient', 'emails')} |",
            f"| Category, **LLM alone** (same model, given the profile, no rules) | {stat('category_llm_only', 'emails')} |",
            f"| Key fields (brand, fee, currency, payment type, deadline) | {stat('fields_ok', 'fields_total')} |",
            f"| Next step per deal, strict | {stat('action_strict', 'deals')} |",
            f"| Next step per deal, lenient | {stat('action_lenient', 'deals')} |",
            f"| Required risk flags found | {stat('flags_hit', 'flags_total')} |",
            f"| Grouping precision / recall (pairs) | {first['grouping']['precision']:.2f} / {first['grouping']['recall']:.2f} "
            f"({first['grouping']['got_groups']} deals, expected {first['grouping']['expected_groups']}) |",
            ""]

    out += ["## Per email (first run)", "", "| Email | Expected | Got | LLM alone | Wrong fields |", "|---|---|---|---|---|"]
    for r in first["rows"]:
        mark = "✅" if r["strict"] else ("🟡" if r["lenient"] else "❌")
        wrong = ", ".join(f for f, ok in r["fields"].items() if not ok) or "-"
        llm_mark = "✅" if r["llm_only_ok"] else "❌"
        out.append(f"| {r['id']} | {r['expected']} | {mark} {r['got']} | {llm_mark} {r['llm_only']} | {wrong} |")
    out += ["", "## Per deal (first run)", "", "| Deal | Expected step | Got | Missing flags | Extra flags |", "|---|---|---|---|---|"]
    for r in first["deal_rows"]:
        mark = "✅" if r["strict"] else ("🟡" if r["lenient"] else "❌")
        out.append(f"| {r['group']} | {r['expected']} | {mark} {r['got']} | {', '.join(r['missing_flags']) or '-'} | {', '.join(r['extra_flags']) or '-'} |")

    if k > 1:
        flaky = defaultdict(set)
        for run in runs:
            for r in run["rows"]:
                flaky[r["id"]].add(r["got"])
        unstable = {eid: cats for eid, cats in flaky.items() if len(cats) > 1}
        out += ["", "## Stability across runs", ""]
        if unstable:
            for eid, cats in sorted(unstable.items()):
                out.append(f"- {eid}: {', '.join(sorted(cats))}")
        else:
            out.append(f"Every email got the same category in all {k} runs.")
    return "\n".join(out) + "\n"
