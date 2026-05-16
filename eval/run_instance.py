"""
eval/run_instance.py
——————————————————————————————————————————————
Run pycc against a single SWE-bench Lite instance and produce a candidate patch.

Usage (standalone):
    python eval/run_instance.py --instance_id astropy__astropy-12907 \
                                 --workdir /tmp/swe_runs \
                                 [--model deepseek/deepseek-v4-pro] \
                                 [--timeout 300]

Output:
    {workdir}/{instance_id}/candidate.patch   — git diff to apply
    {workdir}/{instance_id}/run.log           — full stdout/stderr
    {workdir}/{instance_id}/result.json       — metadata (resolved TBD)
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path


# ── Helpers ───────────────────────────────────────────────────────────────────

def _run(cmd: list[str], cwd: str | None = None, timeout: int = 60,
         capture: bool = True) -> tuple[int, str]:
    """Run a subprocess, return (returncode, combined_output)."""
    r = subprocess.run(
        cmd, cwd=cwd, capture_output=capture, text=True, timeout=timeout,
    )
    out = (r.stdout or "") + (r.stderr or "")
    return r.returncode, out.strip()


def clone_repo(repo: str, base_commit: str, dest: Path, timeout: int = 180) -> None:
    """Clone a GitHub repo at a specific commit into dest."""
    dest.mkdir(parents=True, exist_ok=True)
    url = f"https://github.com/{repo}.git"

    rc, out = _run(["git", "clone", "--depth=50", url, str(dest)], timeout=timeout)
    if rc != 0:
        raise RuntimeError(f"git clone failed: {out}")

    rc, out = _run(["git", "fetch", "--depth=50", "origin", base_commit],
                   cwd=str(dest), timeout=120)
    # fetch may fail if commit is already present — that's fine
    rc, out = _run(["git", "checkout", base_commit], cwd=str(dest), timeout=60)
    if rc != 0:
        raise RuntimeError(f"git checkout {base_commit} failed: {out}")


def run_pycc(problem_statement: str, repo_dir: Path, model: str,
             timeout: int = 300) -> tuple[str, str]:
    """
    Invoke pycc in non-interactive print mode.

    Returns (stdout, stderr).
    """
    prompt = _build_prompt(problem_statement)

    # Always use source pycc.py to avoid stale installed binaries
    pycc_root = Path(__file__).resolve().parent.parent
    cmd = [sys.executable, str(pycc_root / "pycc.py"),
           "--print", "--accept-all", "--model", model, prompt]

    env = dict(os.environ)
    env["PYCC_NO_HISTORY"] = "1"   # skip readline history in batch mode

    r = subprocess.run(
        cmd,
        cwd=str(repo_dir),
        capture_output=True,
        text=True,
        timeout=timeout,
        env=env,
    )
    return r.stdout, r.stderr


def _build_prompt(problem_statement: str) -> str:
    return (
        "You are solving a real GitHub issue. "
        "Read the issue carefully, locate the relevant code, "
        "and implement a minimal fix. "
        "Do NOT add unnecessary tests or documentation. "
        "Make only the changes needed to resolve the issue.\n\n"
        f"ISSUE:\n{problem_statement}"
    )


def extract_patch(repo_dir: Path) -> str:
    """Return `git diff` of all tracked-file modifications."""
    rc, diff = _run(["git", "diff"], cwd=str(repo_dir))
    return diff


# ── Main entry point ──────────────────────────────────────────────────────────

def run_instance(
    instance: dict,
    workdir: Path,
    model: str = "deepseek/deepseek-v4-pro",
    timeout: int = 300,
    skip_if_exists: bool = True,
) -> dict:
    """
    Run pycc on one SWE-bench instance.

    Args:
        instance:       Row from the HuggingFace dataset.
        workdir:        Root directory for all run outputs.
        model:          pycc model string.
        timeout:        Seconds to allow pycc to run.
        skip_if_exists: If True and result.json already exists, skip.

    Returns:
        result dict with keys: instance_id, patch, error, elapsed_s
    """
    iid      = instance["instance_id"]
    repo     = instance["repo"]
    commit   = instance["base_commit"]
    problem  = instance["problem_statement"]

    run_dir  = workdir / iid
    run_dir.mkdir(parents=True, exist_ok=True)

    result_file = run_dir / "result.json"
    if skip_if_exists and result_file.exists():
        with open(result_file) as f:
            return json.load(f)

    log_path   = run_dir / "run.log"
    patch_path = run_dir / "candidate.patch"
    repo_dir   = run_dir / "repo"

    result = {
        "instance_id": iid,
        "model":       model,
        "patch":       "",
        "error":       None,
        "elapsed_s":   0,
    }

    t0 = time.time()
    try:
        # 1. Clone repo at base commit (skip if already done)
        if not (repo_dir / ".git").exists():
            clone_repo(repo, commit, repo_dir, timeout=180)

        # 2. Run pycc
        stdout, stderr = run_pycc(problem, repo_dir, model, timeout=timeout)

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("=== STDOUT ===\n")
            f.write(stdout)
            f.write("\n\n=== STDERR ===\n")
            f.write(stderr)

        # 3. Extract patch
        patch = extract_patch(repo_dir)
        result["patch"] = patch
        patch_path.write_text(patch, encoding="utf-8")

    except Exception as e:
        result["error"] = str(e)
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n\n=== ERROR ===\n{e}\n")

    result["elapsed_s"] = round(time.time() - t0, 1)

    with open(result_file, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    return result


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Run pycc on one SWE-bench instance")
    parser.add_argument("--instance_id", required=True)
    parser.add_argument("--workdir",     required=True)
    parser.add_argument("--model",       default="deepseek/deepseek-v4-pro")
    parser.add_argument("--timeout",     type=int, default=300)
    args = parser.parse_args()

    from datasets import load_dataset
    ds = load_dataset("princeton-nlp/SWE-bench_Lite", split="test")
    matches = [row for row in ds if row["instance_id"] == args.instance_id]
    if not matches:
        print(f"ERROR: instance_id {args.instance_id!r} not found in SWE-bench Lite")
        sys.exit(1)

    result = run_instance(
        matches[0],
        workdir=Path(args.workdir),
        model=args.model,
        timeout=args.timeout,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
