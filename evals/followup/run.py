"""Run the follow-up classifier eval against live Sarvam (D48).

    .venv/Scripts/python evals/followup/run.py                       # 39 tuning cases
    .venv/Scripts/python evals/followup/run.py --cases holdout.jsonl # 12 held-out cases

Writes evals/followup/results/<model>-<cases>-<timestamp>.json and prints a summary.
Target: action accuracy >= 90%, exact >= 85% on held-out.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time
from datetime import datetime

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(HERE))

import httpx  # noqa: E402

from drapefit.config import _read_env  # noqa: E402
from drapefit.models import ItemSpec, RequestSpec  # noqa: E402
from drapefit.parser import ParseError, SarvamParser, followup_context  # noqa: E402
from grade import grade_case  # noqa: E402

CONCURRENCY = 4


def build_contexts() -> dict[str, str]:
    raw = json.loads((HERE / "contexts.json").read_text(encoding="utf-8"))
    out = {}
    for name, ctx in raw.items():
        spec = RequestSpec(items=[ItemSpec(**i) for i in ctx["items"]])
        shown = {int(k): v for k, v in ctx["shown"].items()}
        out[name] = followup_context(spec, shown, ctx["cart"])
    return out


async def run(model: str, case_file: str) -> dict:
    key = _read_env("SARVAM_API_KEY")
    if not key:
        raise SystemExit("SARVAM_API_KEY not set")
    contexts = build_contexts()
    cases = [json.loads(l) for l in (HERE / case_file).read_text(encoding="utf-8").splitlines() if l.strip()]
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=60) as http:
        parser = SarvamParser(http, key, model)

        async def one(case: dict) -> dict:
            async with sem:
                start = time.monotonic()
                try:
                    got = (await parser.classify(case["text"], contexts[case["ctx"]])).follow_up.model_dump()
                except ParseError as err:
                    got = None
                    print(f"  case {case['id']}: failed: {err}")
                latency = int((time.monotonic() - start) * 1000)
            return {"id": case["id"], "text": case["text"], "got": got, "latency_ms": latency,
                    "grade": grade_case(case, got)}

        rows = await asyncio.gather(*(one(c) for c in cases))
    n = len(rows)
    latencies = sorted(r["latency_ms"] for r in rows)
    return {
        "model": model,
        "cases": n,
        "action_accuracy": sum(r["grade"]["action_ok"] for r in rows) / n,
        "exact_match": sum(r["grade"]["exact"] for r in rows) / n,
        "failures_count": sum(r["got"] is None for r in rows),
        "latency_ms_p50": latencies[n // 2],
        "latency_ms_p95": latencies[min(n - 1, int(n * 0.95))],
        "misses": [{"id": r["id"], "text": r["text"], "notes": r["grade"]["notes"]} for r in rows if not r["grade"]["exact"]],
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default=_read_env("SARVAM_MODEL") or "sarvam-105b-conversations")
    ap.add_argument("--cases", default="cases.jsonl")
    args = ap.parse_args()
    result = asyncio.run(run(args.model, args.cases))
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    out = HERE / "results" / f"{args.model}-{pathlib.Path(args.cases).stem}-{stamp}.json"
    out.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"{args.model} on {args.cases}: action {result['action_accuracy']:.0%}, exact {result['exact_match']:.0%} "
          f"({result['cases']} cases), p50 {result['latency_ms_p50']} ms, p95 {result['latency_ms_p95']} ms")
    for miss in result["misses"]:
        print(f"  #{miss['id']} {miss['text']!r}: {'; '.join(miss['notes'])}")
    print(f"saved {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
