"""Command line: `inbox run` and `inbox eval`."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from .baseline import baseline_category
from .evaluate import render_eval, score
from .llm import LLMClient, LLMError
from .models import Email
from .pipeline import Pipeline
from .report import render_summary
from .rules import Profile


class InputError(Exception):
    pass


def _read_json(path: str, what: str):
    try:
        return json.loads(Path(path).read_text())
    except FileNotFoundError:
        raise InputError(f"{what} file not found: {path}")
    except json.JSONDecodeError as e:
        raise InputError(f"{what} file is not valid JSON: {path} ({e})")


def _load(inbox: str, profile: str) -> tuple[list[Email], Profile]:
    try:
        emails = [Email.model_validate(e) for e in _read_json(inbox, "Inbox")]
        return emails, Profile(_read_json(profile, "Profile"))
    except (ValueError, KeyError, TypeError) as e:
        if isinstance(e, InputError):
            raise
        raise InputError(f"Could not read the inbox or profile: {e}")


def _write_outputs(out: Path, deals, results, summary: str) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.md").write_text(summary)
    (out / "deals.json").write_text(json.dumps([d.model_dump(mode="json") for d in deals], indent=2, ensure_ascii=False))
    (out / "emails.json").write_text(json.dumps([r.model_dump(mode="json") for r in results], indent=2, ensure_ascii=False))


def cmd_run(args) -> int:
    emails, profile = _load(args.inbox, args.profile)
    llm = LLMClient.from_env(use_cache=not args.no_cache)
    print(f"Reading {len(emails)} emails with {llm.model} at {llm.base_url}", file=sys.stderr)
    started = time.perf_counter()
    deals, results = Pipeline(profile, llm, llm_drafts=not args.template_drafts).run(
        emails, progress=lambda m: print(m, file=sys.stderr))
    summary = render_summary(profile, deals)
    out = Path(args.out)
    _write_outputs(out, deals, results, summary)
    s = llm.stats
    print(f"\nDone in {time.perf_counter() - started:.1f}s · {s.calls} model calls ({s.cache_hits} cached) · "
          f"{s.prompt_tokens + s.completion_tokens} tokens · wrote {out}/summary.md, deals.json, emails.json\n",
          file=sys.stderr)
    print(summary)
    return 0


def cmd_eval(args) -> int:
    emails, profile = _load(args.inbox, args.profile)
    expected = _read_json(args.expected, "Expected-answers")
    runs, model_secs, model_calls = [], 0.0, 0
    for i in range(args.repeats):
        # Repeats bypass the cache, or they would all be the same run.
        llm = LLMClient.from_env(use_cache=args.repeats == 1 and not args.no_cache)
        print(f"Eval run {i + 1}/{args.repeats} with {llm.model}", file=sys.stderr)
        deals, results = Pipeline(profile, llm, llm_drafts=False).run(
            emails, progress=lambda m: print(m, file=sys.stderr))
        baseline = {e.id: baseline_category(e, profile.data, llm) for e in emails}
        runs.append(score(expected, deals, results, baseline))
        model_secs += sum(r.seconds for r in results)
        model_calls += len(results)
        if i == 0:
            _write_outputs(Path(args.out), deals, results, render_summary(profile, deals))
    report = render_eval(runs, llm.model, model_secs / max(model_calls, 1))
    Path(args.out).mkdir(parents=True, exist_ok=True)
    (Path(args.out) / "eval.md").write_text(report)
    (Path(args.out) / "eval.json").write_text(json.dumps(runs, indent=2, default=str))
    print(report)
    return 0


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    p = argparse.ArgumentParser(prog="inbox", description="Brand-deal inbox assistant for creators.")
    p.add_argument("--inbox", default="data/inbox.json")
    p.add_argument("--profile", default="data/creator_profile.json")
    p.add_argument("--out", default="out")
    p.add_argument("--no-cache", action="store_true", help="ignore cached model answers")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="process the inbox and write the summary")
    r.add_argument("--template-drafts", action="store_true", help="skip the model for drafts, use templates")
    e = sub.add_parser("eval", help="score against eval/expected.json")
    e.add_argument("--expected", default="eval/expected.json")
    e.add_argument("--repeats", type=int, default=1)
    args = p.parse_args(argv)
    try:
        return cmd_run(args) if args.cmd == "run" else cmd_eval(args)
    except InputError as err:
        print(f"error: {err}", file=sys.stderr)
        return 2
    except LLMError as err:
        print(f"Model error: {err}\nCheck LLM_BASE_URL / LLM_MODEL / LLM_API_KEY in .env (see .env.example).",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
