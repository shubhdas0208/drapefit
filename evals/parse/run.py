"""Run the 50-case parse eval against live Sarvam.

    .venv/Scripts/python evals/parse/run.py --model sarvam-105b-conversations

Writes evals/parse/results/<model>-<timestamp>.json and prints a summary.
Targets (design doc): exact match >= 90%; missed clarifications <= 10%.
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
from drapefit.parser import ParseError, SarvamParser  # noqa: E402
from grade import grade_case  # noqa: E402

CONCURRENCY = 4


async def run(model: str, limit: int | None, case_file: str = "cases.jsonl") -> dict:
    key = _read_env("SARVAM_API_KEY")
    if not key:
        raise SystemExit("SARVAM_API_KEY not set")
    cases = [json.loads(line) for line in (HERE / case_file).read_text(encoding="utf-8").splitlines() if line]
    cases = cases[:limit] if limit else cases
    sem = asyncio.Semaphore(CONCURRENCY)
    async with httpx.AsyncClient(timeout=60) as http:
        parser = SarvamParser(http, key, model)

        async def one(case: dict) -> dict:
            async with sem:
                start = time.monotonic()
                try:
                    parsed = await parser.parse(case["text"])
                    spec, attempts = parsed.spec.model_dump(), parsed.attempts
                except ParseError as err:
                    spec, attempts = None, 2
                    print(f"  case {case['id']}: parse failed: {err}")
                latency = int((time.monotonic() - start) * 1000)
            return {"id": case["id"], "text": case["text"], "spec": spec, "attempts": attempts,
                    "latency_ms": latency, "grade": grade_case(case, spec)}

        rows = await asyncio.gather(*(one(c) for c in cases))
    return summarise(model, cases, rows)


def summarise(model: str, cases: list[dict], rows: list[dict]) -> dict:
    n = len(rows)
    fields: dict[str, list[bool]] = {}
    for r in rows:
        for f, oks in r["grade"]["fields"].items():
            fields.setdefault(f, []).extend(oks)
    should_ask = [r for r, c in zip(rows, cases) if c["ambiguous"] is True]
    should_not = [r for r, c in zip(rows, cases) if c["ambiguous"] is False]
    latencies = sorted(r["latency_ms"] for r in rows)
    return {
        "model": model,
        "cases": n,
        "exact_match": sum(r["grade"]["exact"] for r in rows) / n,
        "parse_failures": sum(r["spec"] is None for r in rows),
        "retries": sum(r["attempts"] > 1 for r in rows),
        "field_accuracy": {f: round(sum(v) / len(v), 3) for f, v in sorted(fields.items())},
        "missed_clarifications": (sum(not r["grade"].get("asked") for r in should_ask) / len(should_ask)) if should_ask else None,
        "needless_clarifications": (sum(bool(r["grade"].get("asked")) for r in should_not) / len(should_not)) if should_not else None,
        "latency_ms_p50": latencies[n // 2],
        "latency_ms_p95": latencies[min(n - 1, int(n * 0.95))],
        "failures": [{"id": r["id"], "text": r["text"], "notes": r["grade"]["notes"]} for r in rows if not r["grade"]["exact"]],
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="sarvam-105b-conversations")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--cases", default="cases.jsonl", help="cases.jsonl (tuning) or holdout.jsonl")
    args = ap.parse_args()
    report = asyncio.run(run(args.model, args.limit, args.cases))
    out = HERE / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{args.model}-{pathlib.Path(args.cases).stem}-{datetime.now():%Y%m%d-%H%M%S}.json"
    path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    summary = {k: v for k, v in report.items() if k not in ("rows", "failures")}
    print(json.dumps(summary, indent=1))
    print(f"{len(report['failures'])} non-exact cases:")
    for f in report["failures"]:
        print(f"  #{f['id']} {f['text'][:60]!r}: {'; '.join(f['notes'])[:220]}")
    print("saved", path.relative_to(ROOT))


if __name__ == "__main__":
    main()
