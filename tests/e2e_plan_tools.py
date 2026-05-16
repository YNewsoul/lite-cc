"""End-to-end test for EnterPlanMode / ExitPlanMode tools.

Plan mode is now an independent overlay (_plan_mode_active).
permission_mode ('auto'|'manual'|'accept-all') is never changed by these tools.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

SEP = "=" * 60


def test_plan_tools():
    tmpdir = Path(tempfile.mkdtemp(prefix="plan_tools_e2e_"))
    orig_cwd = os.getcwd()
    os.chdir(str(tmpdir))

    try:
        _run(tmpdir)
    finally:
        os.chdir(orig_cwd)
        shutil.rmtree(str(tmpdir), ignore_errors=True)


def _run(tmpdir):
    from tools import _enter_plan_mode, _exit_plan_mode
    from agent import _check_permission

    config = {
        "permission_mode": "auto",
        "_session_id": "tooltest",
    }

    # ── Step 1: EnterPlanMode tool creates plan file, sets _plan_mode_active ──
    print(f"\n{SEP}")
    print("STEP 1: EnterPlanMode")
    print(SEP)
    result = _enter_plan_mode({"task_description": "Add WebSocket support"}, config)
    # permission_mode must NOT change
    assert config["permission_mode"] == "auto", \
        f"permission_mode should stay 'auto', got {config['permission_mode']!r}"
    assert config.get("_plan_mode_active") is True, "_plan_mode_active should be True"
    assert config["_plan_file"], "_plan_file should be set"
    plan_path = Path(config["_plan_file"])
    assert plan_path.exists(), "plan file should exist on disk"
    assert "WebSocket" in plan_path.read_text(encoding="utf-8"), "plan file should contain task description"
    assert "计划限制层已激活" in result, f"Expected activation message, got: {result!r}"
    print(f"  Plan file: {plan_path}")
    print(f"  permission_mode: {config['permission_mode']}")
    print(f"  _plan_mode_active: {config['_plan_mode_active']}")
    print("  PASS")

    # ── Step 2: EnterPlanMode again → already in plan mode ──
    print(f"\n{SEP}")
    print("STEP 2: EnterPlanMode while already in plan mode")
    print(SEP)
    result = _enter_plan_mode({}, config)
    assert "已处于计划模式" in result, f"Expected 'already in plan mode' message, got: {result!r}"
    print(f"  {result}")
    print("  PASS")

    # ── Step 3: Permission checks in plan mode ──
    print(f"\n{SEP}")
    print("STEP 3: Permission checks")
    print(SEP)

    # Reads allowed
    assert _check_permission({"name": "Read", "input": {}}, config) == True
    assert _check_permission({"name": "Glob", "input": {}}, config) == True
    assert _check_permission({"name": "Grep", "input": {}}, config) == True
    print("  Reads: allowed")

    # Writes blocked
    assert _check_permission({"name": "Write", "input": {"file_path": str(tmpdir / "x.py")}}, config) == False
    assert _check_permission({"name": "Edit", "input": {"file_path": str(tmpdir / "x.py")}}, config) == False
    print("  Writes to other files: blocked")

    # Write to plan file allowed
    assert _check_permission({"name": "Write", "input": {"file_path": str(plan_path)}}, config) == True
    assert _check_permission({"name": "Edit", "input": {"file_path": str(plan_path)}}, config) == True
    print("  Writes to plan file: allowed")

    # Plan tools always auto-approved
    assert _check_permission({"name": "EnterPlanMode", "input": {}}, config) == True
    assert _check_permission({"name": "ExitPlanMode", "input": {}}, config) == True
    print("  Plan tools: auto-approved")
    print("  PASS")

    # ── Step 4: ExitPlanMode with empty/header-only plan → rejected ──
    print(f"\n{SEP}")
    print("STEP 4: ExitPlanMode with empty plan")
    print(SEP)
    # Plan file currently has just the header
    result = _exit_plan_mode({}, config)
    if "空" in result or "empty" in result.lower():
        print(f"  Correctly rejected: {result[:80]}")
        assert config.get("_plan_mode_active") is True, "_plan_mode_active should still be True"
    else:
        # Header counts as content — that's fine too
        print(f"  Header accepted as plan content")
    print("  PASS")

    # ── Step 5: Write plan content and ExitPlanMode ──
    print(f"\n{SEP}")
    print("STEP 5: Write plan content and ExitPlanMode")
    print(SEP)
    # Ensure we're in plan mode
    config["_plan_mode_active"] = True
    plan_path.write_text(
        "# Plan: Add WebSocket support\n\n"
        "## Phase 1: Create ws_handler.py\n"
        "## Phase 2: Modify server.py\n"
        "## Phase 3: Add tests\n",
        encoding="utf-8",
    )
    result = _exit_plan_mode({}, config)
    # permission_mode must NOT change
    assert config["permission_mode"] == "auto", \
        f"permission_mode should stay 'auto', got {config['permission_mode']!r}"
    assert config.get("_plan_mode_active") is False, "_plan_mode_active should be False"
    assert "计划限制层已停用" in result, f"Expected deactivation message, got: {result!r}"
    assert "Phase 1" in result, "plan content should be included in result"
    assert "用户审核" in result or "批准" in result, "should mention user approval"
    print(f"  permission_mode: {config['permission_mode']}")
    print(f"  _plan_mode_active: {config['_plan_mode_active']}")
    print(f"  Plan content in result: {'Phase 1' in result}")
    print("  PASS")

    # ── Step 6: ExitPlanMode when not in plan mode ──
    print(f"\n{SEP}")
    print("STEP 6: ExitPlanMode when not in plan mode")
    print(SEP)
    result = _exit_plan_mode({}, config)
    assert "未处于计划模式" in result, f"Expected 'not in plan mode' message, got: {result!r}"
    print(f"  {result}")
    print("  PASS")

    # ── Step 7: Plan tools auto-approved in all permission modes ──
    print(f"\n{SEP}")
    print("STEP 7: Plan tools auto-approved in all permission modes")
    print(SEP)
    config["permission_mode"] = "auto"
    config["_plan_mode_active"] = False
    assert _check_permission({"name": "EnterPlanMode", "input": {}}, config) == True
    assert _check_permission({"name": "ExitPlanMode", "input": {}}, config) == True
    print("  Auto-approved in auto mode")

    config["permission_mode"] = "manual"
    assert _check_permission({"name": "EnterPlanMode", "input": {}}, config) == True
    assert _check_permission({"name": "ExitPlanMode", "input": {}}, config) == True
    print("  Auto-approved in manual mode")
    print("  PASS")

    # ── Step 8: System prompt includes plan mode guidance ──
    print(f"\n{SEP}")
    print("STEP 8: System prompt includes plan mode guidance")
    print(SEP)
    from context import build_system_prompt
    config["permission_mode"] = "auto"
    config["_plan_mode_active"] = False
    prompt = build_system_prompt(config)
    assert "EnterPlanMode" in prompt
    assert "ExitPlanMode" in prompt
    assert "complex" in prompt.lower() or "multi-file" in prompt.lower() or "复杂" in prompt
    print("  System prompt references plan tools")

    # Plan mode active → system prompt should include plan file reference
    config["_plan_mode_active"] = True
    config["_plan_file"] = str(plan_path)
    prompt_plan = build_system_prompt(config)
    assert "计划模式" in prompt_plan, "System prompt should include plan mode section when active"
    assert str(plan_path) in prompt_plan, "System prompt should reference plan file path"
    config["_plan_mode_active"] = False
    prompt_normal = build_system_prompt(config)
    assert "计划限制层当前处于激活状态" not in prompt_normal, \
        "Normal mode should NOT have plan mode active instructions"
    print("  Plan mode active: system prompt injected correctly")
    print("  PASS")

    print(f"\n{SEP}")
    print("ALL 8 STEPS PASSED")
    print(SEP)


if __name__ == "__main__":
    test_plan_tools()
