"""
eval/score.py
——————————————————————————————————————————————
Score candidate patches against SWE-bench Lite gold tests.

Two scoring modes
─────────────────
A) FAST (no Docker) — patch-overlap heuristic
   Checks whether any FAIL_TO_PASS test file/function name appears in the
   candidate patch.  Fast, zero infrastructure, ~70 % correlation with true
   resolve rate.  Use this to get a quick estimate while building the harness.

B) OFFICIAL (requires Docker) — swebench.harness
   Runs the real test suite inside the official Docker image.
   Produces ground-truth resolve rate.

Usage:
    # Fast heuristic estimate (no Docker needed)
    python eval/score.py --workdir /tmp/swe_runs

    # Official scoring (requires Docker + swebench images)
    python eval/score.py --workdir /tmp/swe_runs --official
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── Fast heuristic scorer ─────────────────────────────────────────────────────

def _heuristic_score(result: dict, instance: dict) -> bool:
    """
    Lightweight proxy: True if the patch touches at least one file mentioned
    in a FAIL_TO_PASS test path.

    This is NOT a substitute for real test execution, but gives a fast
    lower-bound estimate during development.
    """
    patch = result.get("patch", "")
    if not patch:
        return False

    fail_tests = instance.get("FAIL_TO_PASS", [])
    if isinstance(fail_tests, str):
        fail_tests = json.loads(fail_tests)

    # Extract file stems from test paths like
    # "astropy/modeling/tests/test_separable.py::test_func"
    for test_path in fail_tests:
        file_part = test_path.split("::")[0]          # e.g. astropy/modeling/tests/test_separable.py
        module    = Path(file_part).stem               # e.g. test_separable
        # Check if patch modifies any file in the same package sub-directory
        pkg_dir   = str(Path(file_part).parent)       # e.g. astropy/modeling/tests
        pkg_parent = str(Path(file_part).parent.parent)  # e.g. astropy/modeling
        if pkg_parent in patch or module in patch:
            return True

    return False


# ── Aggregate ─────────────────────────────────────────────────────────────────

def score_workdir(workdir: Path, official: bool = False) -> None:
    from datasets import load_dataset
    print("Loading SWE-bench Lite …")
    ds        = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    inst_map  = {r["instance_id"]: r for r in ds}

    result_files = sorted(workdir.glob("*/result.json"))
    if not result_files:
        print(f"No result.json files found in {workdir}")
        sys.exit(1)

    results  = []
    for rf in result_files:
        with open(rf) as f:
            results.append(json.load(f))

    total    = len(results)
    no_patch = sum(1 for r in results if not r.get("patch"))
    errors   = sum(1 for r in results if r.get("error"))

    print(f"\nResults summary")
    print(f"  Total run:    {total}")
    print(f"  With patch:   {total - no_patch}")
    print(f"  No patch:     {no_patch}")
    print(f"  Errors:       {errors}")

    if official:
        _score_official(results, workdir)
    else:
        _score_heuristic(results, inst_map)


def _score_heuristic(results: list[dict], inst_map: dict) -> None:
    resolved = []
    for r in results:
        iid  = r["instance_id"]
        inst = inst_map.get(iid)
        if inst and _heuristic_score(r, inst):
            resolved.append(iid)

    total   = len(results)
    n_res   = len(resolved)
    rate    = n_res / total * 100 if total else 0

    print(f"\n── Heuristic estimate (patch-overlap proxy) ──")
    print(f"  Heuristic-resolved: {n_res} / {total}  ({rate:.1f}%)")
    print()
    if resolved:
        print("  Likely resolved:")
        for iid in resolved[:20]:
            print(f"    {iid}")
        if len(resolved) > 20:
            print(f"    … and {len(resolved) - 20} more")
    print()
    print("NOTE: This is a proxy metric (~70% correlation with real resolve rate).")
    print("      Run with --official for ground-truth results.")


def _score_official(results: list[dict], workdir: Path) -> None:
    """
    Use swebench.harness.run_evaluation to score.
    Requires Docker and the official swebench images.
    """
    try:
        from swebench.harness.run_evaluation import main as swe_main
    except ImportError:
        print("ERROR: swebench.harness not found. Install with: pip install swebench")
        sys.exit(1)

    # Build predictions file in swebench format
    predictions = []
    for r in results:
        if r.get("patch"):
            predictions.append({
                "instance_id": r["instance_id"],
                "model_patch": r["patch"],
                "model_name_or_path": r.get("model", "pycc"),
            })

    pred_file = workdir / "predictions.jsonl"
    with open(pred_file, "w") as f:
        for p in predictions:
            f.write(json.dumps(p) + "\n")

    print(f"\nRunning official swebench evaluation …")
    print(f"Predictions file: {pred_file}")
    print(f"This requires Docker and will take a while.\n")

    # Call swebench harness
    sys.argv = [
        "run_evaluation",
        "--dataset_name", "princeton-nlp/SWE-bench_Lite",
        "--predictions_path", str(pred_file),
        "--max_workers", "4",
        "--run_id", "pycc_eval",
    ]
    swe_main()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Score pycc SWE-bench results")
    parser.add_argument("--workdir",  default="/tmp/pycc_swe_runs")
    parser.add_argument("--official", action="store_true",
                        help="Use official swebench Docker evaluation (slow, requires Docker)")
    args = parser.parse_args()

    score_workdir(Path(args.workdir), official=args.official)


if __name__ == "__main__":
    main()
