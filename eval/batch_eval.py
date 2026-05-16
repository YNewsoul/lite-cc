"""
eval/batch_eval.py
——————————————————————————————————————————————
Batch-run pycc against a subset (or full set) of SWE-bench Lite.

Usage:
    # Run first 30 instances, 3 workers in parallel
    python eval/batch_eval.py --n 30 --workers 3 --workdir /tmp/swe_runs

    # Run all 300
    python eval/batch_eval.py --workers 3 --workdir /tmp/swe_runs

    # Run specific instances
    python eval/batch_eval.py --ids astropy__astropy-12907 django__django-11099

    # Resume (skips instances with existing result.json)
    python eval/batch_eval.py --n 30 --workdir /tmp/swe_runs

After running, call score.py to get the resolve rate.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main():
    parser = argparse.ArgumentParser(description="Batch-run pycc on SWE-bench Lite")
    parser.add_argument("--workdir",  default="/tmp/pycc_swe_runs",
                        help="Directory to store all run outputs (default: /tmp/pycc_swe_runs)")
    parser.add_argument("--n",        type=int, default=None,
                        help="Number of instances to run (default: all 300)")
    parser.add_argument("--workers",  type=int, default=2,
                        help="Parallel workers (default: 2; limited by API rate)")
    parser.add_argument("--model",    default="deepseek/deepseek-v4-pro",
                        help="pycc model string")
    parser.add_argument("--timeout",  type=int, default=300,
                        help="Per-instance timeout in seconds (default: 300)")
    parser.add_argument("--ids",      nargs="*",
                        help="Specific instance IDs to run (overrides --n)")
    parser.add_argument("--no-skip",  action="store_true",
                        help="Re-run even if result.json already exists")
    args = parser.parse_args()

    workdir = Path(args.workdir)
    workdir.mkdir(parents=True, exist_ok=True)

    # Load dataset
    print("Loading SWE-bench Lite dataset …")
    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    instances = list(ds)

    # Filter
    if args.ids:
        id_set    = set(args.ids)
        instances = [r for r in instances if r["instance_id"] in id_set]
        if not instances:
            print(f"ERROR: none of {args.ids} found in dataset")
            sys.exit(1)
    elif args.n:
        instances = instances[: args.n]

    total = len(instances)
    print(f"Running {total} instances  |  workers={args.workers}  |  model={args.model}")
    print(f"Output directory: {workdir}\n")

    from eval.run_instance import run_instance

    completed = 0
    errors    = 0
    t_start   = time.time()

    def _run_one(inst: dict) -> dict:
        return run_instance(
            inst, workdir,
            model=args.model,
            timeout=args.timeout,
            skip_if_exists=not args.no_skip,
        )

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(_run_one, inst): inst for inst in instances}
        for fut in as_completed(futures):
            inst   = futures[fut]
            iid    = inst["instance_id"]
            completed += 1
            try:
                result = fut.result()
                patch_len = len(result.get("patch", ""))
                elapsed   = result.get("elapsed_s", 0)
                err       = result.get("error")
                if err:
                    errors += 1
                    status = f"ERROR  {err[:60]}"
                elif patch_len == 0:
                    status = "no patch"
                else:
                    status = f"patch={patch_len:,} chars  t={elapsed}s"
                print(f"[{completed:3}/{total}] {iid:<50} {status}")
            except Exception as exc:
                errors += 1
                print(f"[{completed:3}/{total}] {iid:<50} EXCEPTION: {exc}")

    elapsed_total = time.time() - t_start
    print(f"\nDone: {completed} instances in {elapsed_total:.0f}s  |  errors={errors}")
    print(f"Run score.py --workdir {workdir} to get the resolve rate.")


if __name__ == "__main__":
    main()
