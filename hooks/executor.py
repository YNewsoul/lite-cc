"""执行单个钩子 Shell 命令，并返回解析后的 JSON 输出。"""
from __future__ import annotations

import json
import subprocess
import sys


def run_hook(command: str, stdin_data: dict, timeout: int = 10) -> dict | None:
    """执行钩子命令，将 stdin_data 以 JSON 格式传入标准输入。

    参数:
        command:    Shell 命令字符串（使用 shell=True 运行）
        stdin_data: 序列化为 JSON 并传递给标准输入的字典
        timeout:    进程被强制终止前的超时秒数（默认 10 秒）

    返回:
        从标准输出解析出的 JSON 字典，出错 / 非零退出时返回 None。

    副作用:
        执行失败时（非零退出码、超时、异常）向标准错误输出警告信息。
    """
    try:
        proc = subprocess.run(
            command,
            shell=True,
            input=json.dumps(stdin_data).encode(),
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        print(f"[hooks] 警告：钩子执行超时（{timeout}秒）：{command!r}", file=sys.stderr)
        return None
    except Exception as exc:
        print(f"[hooks] 警告：钩子启动失败：{exc}", file=sys.stderr)
        return None

    if proc.returncode != 0:
        stderr_text = proc.stderr.decode(errors="replace").strip()
        msg = f"[hooks] 警告：钩子退出码 {proc.returncode}：{command!r}"
        if stderr_text:
            msg += f"\n  错误输出：{stderr_text[:300]}"
        print(msg, file=sys.stderr)
        return None

    stdout = proc.stdout.decode(errors="replace").strip()
    if not stdout:
        return {}

    try:
        return json.loads(stdout)
    except json.JSONDecodeError:
        # 钩子输出了非 JSON 内容 —— 忽略输出，视为无决策的成功执行
        return {}