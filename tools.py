"""pycc 的工具定义与实现。"""
import json
import os
import re
import glob as _glob
import difflib
import subprocess
import threading
from pathlib import Path
from typing import Callable, Optional

from tool_registry import ToolDef, register_tool
from tool_registry import execute_tool as _registry_execute

# ── AskUserQuestion 状态 ──────────────────────────────────────────────────────
# 主 REPL 循环从 _pending_questions 取出问题，并将答案存入 _question_answers。
_pending_questions: list[dict] = []   # [{id, question, options, allow_freetext, event, result_holder}]
_ask_lock = threading.Lock()

# ── 工具 JSON 结构（发送给 Claude API）─────────────────────────────────

TOOL_SCHEMAS = [
    {
        "name": "Read",
        "description": (
            "读取文件内容。返回带行号的内容（格式：'N\\tline'）。"
            "使用 limit/offset 分块读取大文件。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "绝对文件路径"},
                "limit":     {"type": "integer", "description": "最大读取行数"},
                "offset":    {"type": "integer", "description": "起始行（从 0 开始）"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Write",
        "description": "写入内容到文件，自动创建父目录。",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content":   {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Edit",
        "description": (
            "精确替换文件中的文本。old_string 必须完全匹配（包括空格）。"
            "如果 old_string 出现多次，使用 replace_all=true 或增加上下文。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path":   {"type": "string"},
                "old_string":  {"type": "string", "description": "要替换的精确文本"},
                "new_string":  {"type": "string", "description": "替换文本"},
                "replace_all": {"type": "boolean", "description": "替换所有匹配项"},
            },
            "required": ["file_path", "old_string", "new_string"],
        },
    },
    {
        "name": "Bash",
        "description": "执行 shell 命令。返回标准输出+错误输出。无状态（不保留 cd 切换）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer", "description": "超时秒数（默认30）。安装包、构建等长命令使用 120-300。"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Glob",
        "description": "查找匹配通配符的文件。返回排序后的路径列表。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "通配符，例如 **/*.py"},
                "path":    {"type": "string", "description": "基础目录（默认：当前工作目录）"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "Grep",
        "description": "使用正则搜索文件内容，优先使用 ripgrep，降级为 grep。",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern":      {"type": "string", "description": "正则表达式"},
                "path":         {"type": "string", "description": "要搜索的文件或目录"},
                "glob":         {"type": "string", "description": "文件过滤，例如 *.py"},
                "output_mode":  {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": "content=匹配行，files_with_matches=文件路径，count=匹配次数",
                },
                "case_insensitive": {"type": "boolean"},
                "context":      {"type": "integer", "description": "匹配结果的上下文行数"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "WebFetch",
        "description": "获取 URL 内容并返回纯文本（去除 HTML）。",
        "input_schema": {
            "type": "object",
            "properties": {
                "url":    {"type": "string"},
                "prompt": {"type": "string", "description": "提取内容的提示"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "WebSearch",
        "description": "通过 DuckDuckGo 网页搜索并返回顶部结果。",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
            "required": ["query"],
        },
    },
    # ── 任务工具（结构也在此列出供 Claude 使用）──────────────────
    {
        "name": "TaskCreate",
        "description": (
            "在任务列表中创建新任务。"
            "用于跟踪工作项、待办事项和多步骤计划。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject":     {"type": "string", "description": "简短标题"},
                "description": {"type": "string", "description": "需要完成的内容"},
                "active_form": {"type": "string", "description": "进行中的状态标签"},
                "metadata":    {"type": "object", "description": "任意元数据"},
            },
            "required": ["subject", "description"],
        },
    },
    {
        "name": "TaskUpdate",
        "description": (
            "更新任务：修改状态、标题、描述、负责人、依赖或元数据。"
            "设置 status='deleted' 可删除。"
            "状态：pending, in_progress, completed, cancelled, deleted。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id":       {"type": "string"},
                "subject":       {"type": "string"},
                "description":   {"type": "string"},
                "status":        {"type": "string", "enum": ["pending","in_progress","completed","cancelled","deleted"]},
                "active_form":   {"type": "string"},
                "owner":         {"type": "string"},
                "add_blocks":    {"type": "array", "items": {"type": "string"}},
                "add_blocked_by":{"type": "array", "items": {"type": "string"}},
                "metadata":      {"type": "object"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "TaskGet",
        "description": "通过 ID 获取单个任务的完整信息。",
        "input_schema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "description": "要获取的任务 ID"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "TaskList",
        "description": "列出所有任务及其状态、负责人和阻塞依赖。",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "NotebookEdit",
        "description": (
            "编辑 Jupyter 笔记本（.ipynb）单元格。"
            "支持替换、插入、删除操作。"
            "先使用 Read 工具查看单元格 ID。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "notebook_path": {
                    "type": "string",
                    "description": ".ipynb 文件的绝对路径",
                },
                "new_source": {
                    "type": "string",
                    "description": "单元格的新源代码/文本",
                },
                "cell_id": {
                    "type": "string",
                    "description": (
                        "要编辑的单元格 ID。插入模式下，新单元格会添加在该单元格之后。"
                        "无 ID 时使用 cell-N（从 0 开始）。"
                    ),
                },
                "cell_type": {
                    "type": "string",
                    "enum": ["code", "markdown"],
                    "description": "单元格类型。插入必填，替换默认沿用原类型。",
                },
                "edit_mode": {
                    "type": "string",
                    "enum": ["replace", "insert", "delete"],
                    "description": "replace（默认）/ insert / delete",
                },
            },
            "required": ["notebook_path", "new_source"],
        },
    },
    {
        "name": "GetDiagnostics",
        "description": (
            "获取代码文件的诊断信息（错误、警告）。"
            "Python 使用 pyright/mypy/flake8，TS/JS 使用 tsc，脚本使用 shellcheck。"
            "返回结构化诊断结果。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "要诊断的文件路径",
                },
                "language": {
                    "type": "string",
                    "description": (
                        "覆盖自动检测的语言：python, javascript, typescript, shellscript。"
                        "留空自动根据后缀识别。"
                    ),
                },
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "AskUserQuestion",
        "description": (
            "暂停执行并向用户提问。"
            "需要用户决策时使用。"
            "返回用户的回答字符串。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "向用户提出的问题。",
                },
                "options": {
                    "type": "array",
                    "description": "可选选项列表。每项：{label, description}。",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label":       {"type": "string"},
                            "description": {"type": "string"},
                        },
                        "required": ["label"],
                    },
                },
                "allow_freetext": {
                    "type": "boolean",
                    "description": "如果为 true（默认），用户可以输入自定义文本。",
                },
            },
            "required": ["question"],
        },
    },
    {
        "name": "SleepTimer",
        "description": (
            "设置后台定时器。计时结束后自动向对话插入系统提示："
            "'计时器已完成...'，用于唤醒并执行延迟的监控任务。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "seconds": {"type": "integer", "description": "休眠秒数"}
            },
            "required": ["seconds"],
        },
    },
]

# ── 安全 bash 命令（无需权限确认）──────────────────────────────

_SAFE_PREFIXES = (
    "ls", "cat", "head", "tail", "wc", "pwd", "echo", "printf", "date",
    "which", "type", "env", "printenv", "uname", "whoami", "id",
    "git log", "git status", "git diff", "git show", "git branch",
    "git remote", "git stash list", "git tag",
    "find ", "grep ", "rg ", "ag ", "fd ",
    "python ", "python3 ", "node ", "ruby ", "perl ",
    "pip show", "pip list", "npm list", "cargo metadata",
    "df ", "du ", "free ", "top -bn", "ps ",
    "curl -I", "curl --head",
)

def _is_safe_bash(cmd: str) -> bool:
    """判断命令是否可自动批准执行。委托给 bash 分析器。"""
    try:
        from security.bash_analyzer import analyze_bash, BashRiskLevel
        risk, _ = analyze_bash(cmd)
        return risk == BashRiskLevel.safe
    except Exception:
        # 分析器不可用时降级为前缀检查
        c = cmd.strip()
        return any(c.startswith(p) for p in _SAFE_PREFIXES)


# ── 差异对比工具 ──────────────────────────────────────────────────────────

def generate_unified_diff(old, new, filename, context_lines=3):
    old_lines = old.splitlines(keepends=True)
    new_lines = new.splitlines(keepends=True)
    diff = difflib.unified_diff(old_lines, new_lines,
        fromfile=f"a/{filename}", tofile=f"b/{filename}", n=context_lines)
    return "".join(diff)

def maybe_truncate_diff(diff_text, max_lines=80):
    lines = diff_text.splitlines()
    if len(lines) <= max_lines:
        return diff_text
    shown = lines[:max_lines]
    remaining = len(lines) - max_lines
    return "\n".join(shown) + f"\n\n[... 还有 {remaining} 行 ...]"


# ── 工具实现 ───────────────────────────────────────────────────────────

def _read(file_path: str, limit: int = None, offset: int = None) -> str:
    p = Path(file_path)
    if not p.exists():
        return f"错误：文件不存在：{file_path}"
    if p.is_dir():
        return f"错误：{file_path} 是目录"
    try:
        lines = p.read_text(encoding="utf-8", errors="replace", newline="").splitlines(keepends=True)
        start = offset or 0
        chunk = lines[start:start + limit] if limit else lines[start:]
        if not chunk:
            return "(空文件)"
        return "".join(f"{start + i + 1:6}\t{l}" for i, l in enumerate(chunk))
    except Exception as e:
        return f"错误：{e}"


def _write(file_path: str, content: str) -> str:
    p = Path(file_path)
    try:
        is_new = not p.exists()
        old_content = "" if is_new else p.read_text(encoding="utf-8", errors="replace", newline="")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8", newline="")
        if is_new:
            lc = content.count("\n") + (1 if content and not content.endswith("\n") else 0)
            return f"已创建 {file_path}（{lc} 行）"
        filename = p.name
        diff = generate_unified_diff(old_content, content, filename)
        if not diff:
            return f"{file_path} 无变化"
        truncated = maybe_truncate_diff(diff)
        return f"文件已更新 — {file_path}：\n\n{truncated}"
    except Exception as e:
        return f"错误：{e}"


def _edit(file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> str:
    p = Path(file_path)
    if not p.exists():
        return f"错误：文件不存在：{file_path}"
    try:
        content = p.read_text(encoding="utf-8", errors="replace", newline="")
        
        crlf_count = content.count("\r\n")
        lf_count = content.count("\n")
        is_pure_crlf = crlf_count > 0 and crlf_count == lf_count

        content_norm = content.replace("\r\n", "\n")
        old_norm = old_string.replace("\r\n", "\n")
        new_norm = new_string.replace("\r\n", "\n")

        count = content_norm.count(old_norm)
        if count == 0:
            return "错误：未在文件中找到 old_string。请确保完全匹配，包括空格、缩进和换行。"
        if count > 1 and not replace_all:
            return (f"错误：old_string 出现 {count} 次。"
                    "提供更多上下文使其唯一，或使用 replace_all=true。")

        old_content_norm = content_norm
        new_content_norm = content_norm.replace(old_norm, new_norm) if replace_all else \
                           content_norm.replace(old_norm, new_norm, 1)

        if is_pure_crlf:
            final_content = new_content_norm.replace("\n", "\r\n")
            old_content_final = content
        else:
            final_content = new_content_norm
            old_content_final = content_norm
                      
        p.write_text(final_content, encoding="utf-8", newline="")
        filename = p.name
        diff = generate_unified_diff(old_content_final, final_content, filename)
        return f"已应用修改到 {filename}：\n\n{diff}"
    except Exception as e:
        return f"错误：{e}"


def _kill_proc_tree(pid: int):
    """杀死进程及其所有子进程。"""
    import sys as _sys
    if _sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                       capture_output=True)
    else:
        import signal
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass


def _bash(command: str, timeout: int = 30) -> str:
    import sys as _sys
    kwargs = dict(
        shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, cwd=os.getcwd(),
    )
    if _sys.platform != "win32":
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(command, **kwargs)
        try:
            stdout, stderr = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            _kill_proc_tree(proc.pid)
            proc.wait()
            return f"错误：超时 {timeout} 秒（进程已杀死）"
        out = stdout
        if stderr:
            out += ("\n" if out else "") + "[错误输出]\n" + stderr
        return out.strip() or "(无输出)"
    except Exception as e:
        return f"错误：{e}"


def _glob(pattern: str, path: str = None) -> str:
    base = Path(path) if path else Path.cwd()
    try:
        matches = sorted(base.glob(pattern))
        if not matches:
            return "未匹配到文件"
        return "\n".join(str(m) for m in matches[:500])
    except Exception as e:
        return f"错误：{e}"


def _has_rg() -> bool:
    try:
        subprocess.run(["rg", "--version"], capture_output=True, check=True)
        return True
    except Exception:
        return False


def _grep(pattern: str, path: str = None, glob: str = None,
          output_mode: str = "files_with_matches",
          case_insensitive: bool = False, context: int = 0) -> str:
    use_rg = _has_rg()
    cmd = ["rg" if use_rg else "grep", "--no-heading"]
    if case_insensitive:
        cmd.append("-i")
    if output_mode == "files_with_matches":
        cmd.append("-l")
    elif output_mode == "count":
        cmd.append("-c")
    else:
        cmd.append("-n")
        if context:
            cmd += ["-C", str(context)]
    if glob:
        cmd += (["--glob", glob] if use_rg else ["--include", glob])
    cmd.append(pattern)
    cmd.append(path or str(Path.cwd()))
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        out = r.stdout.strip()
        return out[:20000] if out else "未找到匹配内容"
    except Exception as e:
        return f"错误：{e}"


def _webfetch(url: str, prompt: str = None) -> str:
    try:
        import httpx
        r = httpx.get(url, headers={"User-Agent": "NanoClaude/1.0"},
                      timeout=30, follow_redirects=True)
        r.raise_for_status()
        ct = r.headers.get("content-type", "")
        if "html" in ct:
            text = re.sub(r"<script[^>]*>.*?</script>", "", r.text,
                          flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<style[^>]*>.*?</style>", "", text,
                          flags=re.DOTALL | re.IGNORECASE)
            text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\s+", " ", text).strip()
        else:
            text = r.text
        return text[:25000]
    except ImportError:
        return "错误：未安装 httpx — 执行：pip install httpx"
    except Exception as e:
        return f"错误：{e}"


def _websearch(query: str) -> str:
    try:
        import httpx
        url = "https://html.duckduckgo.com/html/"
        r = httpx.get(url, params={"q": query},
                      headers={"User-Agent": "Mozilla/5.0 (compatible)"},
                      timeout=30, follow_redirects=True)
        titles   = re.findall(r'class="result__title"[^>]*>.*?<a[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                               r.text, re.DOTALL)
        snippets = re.findall(r'class="result__snippet"[^>]*>(.*?)</div>', r.text, re.DOTALL)
        results = []
        for i, (link, title) in enumerate(titles[:8]):
            t = re.sub(r"<[^>]+>", "", title).strip()
            s = re.sub(r"<[^>]+>", "", snippets[i]).strip() if i < len(snippets) else ""
            results.append(f"**{t}**\n{link}\n{s}")
        return "\n\n".join(results) if results else "未找到结果"
    except ImportError:
        return "错误：未安装 httpx — 执行：pip install httpx"
    except Exception as e:
        return f"错误：{e}"


# ── NotebookEdit 实现 ────────────────────────────────────────────

def _parse_cell_id(cell_id: str) -> int | None:
    """将 cell-N 格式转为索引；非该格式返回 None。"""
    m = re.fullmatch(r"cell-(\d+)", cell_id)
    return int(m.group(1)) if m else None


def _notebook_edit(
    notebook_path: str,
    new_source: str,
    cell_id: str = None,
    cell_type: str = None,
    edit_mode: str = "replace",
) -> str:
    p = Path(notebook_path)
    if p.suffix != ".ipynb":
        return "错误：必须是 Jupyter 笔记本文件（.ipynb）"
    if not p.exists():
        return f"错误：笔记本不存在：{notebook_path}"

    try:
        nb = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        return f"错误：笔记本不是合法 JSON：{e}"

    cells = nb.get("cells", [])

    # 解析单元格索引
    def _resolve_index(cid: str) -> int | None:
        for i, c in enumerate(cells):
            if c.get("id") == cid:
                return i
        idx = _parse_cell_id(cid)
        if idx is not None and 0 <= idx < len(cells):
            return idx
        return None

    if edit_mode == "replace":
        if not cell_id:
            return "错误：replace 模式需要 cell_id"
        idx = _resolve_index(cell_id)
        if idx is None:
            return f"错误：未找到单元格 '{cell_id}'"
        target = cells[idx]
        target["source"] = new_source
        if cell_type and cell_type != target.get("cell_type"):
            target["cell_type"] = cell_type
        if target.get("cell_type") == "code":
            target["execution_count"] = None
            target["outputs"] = []

    elif edit_mode == "insert":
        if not cell_type:
            return "错误：insert 模式需要 cell_type（code 或 markdown）"
        nbformat = nb.get("nbformat", 4)
        nbformat_minor = nb.get("nbformat_minor", 0)
        use_ids = nbformat > 4 or (nbformat == 4 and nbformat_minor >= 5)
        new_id = None
        if use_ids:
            import random, string
            new_id = "".join(random.choices(string.ascii_lowercase + string.digits, k=8))

        if cell_type == "markdown":
            new_cell = {"cell_type": "markdown", "source": new_source, "metadata": {}}
        else:
            new_cell = {
                "cell_type": "code",
                "source": new_source,
                "metadata": {},
                "execution_count": None,
                "outputs": [],
            }
        if use_ids and new_id:
            new_cell["id"] = new_id

        if cell_id:
            idx = _resolve_index(cell_id)
            if idx is None:
                return f"错误：未找到单元格 '{cell_id}'"
            cells.insert(idx + 1, new_cell)
        else:
            cells.insert(0, new_cell)
        nb["cells"] = cells
        cell_id = new_id or cell_id

    elif edit_mode == "delete":
        if not cell_id:
            return "错误：delete 模式需要 cell_id"
        idx = _resolve_index(cell_id)
        if idx is None:
            return f"错误：未找到单元格 '{cell_id}'"
        cells.pop(idx)
        nb["cells"] = cells
        p.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
        return f"已从 {notebook_path} 删除单元格 '{cell_id}'"
    else:
        return f"错误：未知编辑模式 '{edit_mode}' — 请使用 replace, insert, delete"

    nb["cells"] = cells
    p.write_text(json.dumps(nb, indent=1, ensure_ascii=False), encoding="utf-8")
    return f"已对 {notebook_path} 中的单元格 '{cell_id}' 应用 NotebookEdit({edit_mode})"


# ── GetDiagnostics 实现 ──────────────────────────────────────────

def _detect_language(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    return {
        ".py":   "python",
        ".js":   "javascript",
        ".mjs":  "javascript",
        ".cjs":  "javascript",
        ".ts":   "typescript",
        ".tsx":  "typescript",
        ".sh":   "shellscript",
        ".bash": "shellscript",
        ".zsh":  "shellscript",
    }.get(ext, "unknown")


def _run_quietly(cmd: list[str], cwd: str | None = None, timeout: int = 30) -> tuple[int, str]:
    """执行命令，返回 (返回码, 合并输出)。"""
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
            cwd=cwd or os.getcwd(),
        )
        out = (r.stdout + ("\n" + r.stderr if r.stderr else "")).strip()
        return r.returncode, out
    except FileNotFoundError:
        return -1, f"(未找到命令：{cmd[0]})"
    except subprocess.TimeoutExpired:
        return -1, f"(超时 {timeout} 秒)"
    except Exception as e:
        return -1, f"(错误：{e})"


def _get_diagnostics(file_path: str, language: str = None) -> str:
    p = Path(file_path)
    if not p.exists():
        return f"错误：文件不存在：{file_path}"

    lang = language or _detect_language(file_path)
    abs_path = str(p.resolve())
    results: list[str] = []

    if lang == "python":
        rc, out = _run_quietly(["pyright", "--outputjson", abs_path])
        if rc != -1:
            try:
                data = json.loads(out)
                diags = data.get("generalDiagnostics", [])
                if not diags:
                    results.append("pyright：无诊断信息")
                else:
                    lines = [f"pyright（发现 {len(diags)} 个问题）："]
                    for d in diags[:50]:
                        rng = d.get("range", {}).get("start", {})
                        ln = rng.get("line", 0) + 1
                        ch = rng.get("character", 0) + 1
                        sev = d.get("severity", "error")
                        msg = d.get("message", "")
                        rule = d.get("rule", "")
                        lines.append(f"  {ln}:{ch} [{sev}] {msg}" + (f" ({rule})" if rule else ""))
                    results.append("\n".join(lines))
            except json.JSONDecodeError:
                if out:
                    results.append(f"pyright：\n{out[:3000]}")
        else:
            rc2, out2 = _run_quietly(["mypy", "--no-error-summary", abs_path])
            if rc2 != -1:
                results.append(f"mypy：\n{out2[:3000]}" if out2 else "mypy：无诊断信息")
            else:
                rc3, out3 = _run_quietly(["flake8", abs_path])
                if rc3 != -1:
                    results.append(f"flake8：\n{out3[:3000]}" if out3 else "flake8：无诊断信息")
                else:
                    rc4, out4 = _run_quietly(["python3", "-m", "py_compile", abs_path])
                    if out4:
                        results.append(f"py_compile（语法检查）：\n{out4}")
                    else:
                        results.append("py_compile：语法正常（无更多可用工具）")

    elif lang in ("javascript", "typescript"):
        rc, out = _run_quietly(["tsc", "--noEmit", "--strict", abs_path])
        if rc != -1:
            results.append(f"tsc：\n{out[:3000]}" if out else "tsc：无错误")
        else:
            rc2, out2 = _run_quietly(["eslint", abs_path])
            if rc2 != -1:
                results.append(f"eslint：\n{out2[:3000]}" if out2 else "eslint：无问题")
            else:
                results.append("未找到 TypeScript/JavaScript 检查工具（安装 tsc 或 eslint）")

    elif lang == "shellscript":
        rc, out = _run_quietly(["shellcheck", abs_path])
        if rc != -1:
            results.append(f"shellcheck：\n{out[:3000]}" if out else "shellcheck：无问题")
        else:
            rc2, out2 = _run_quietly(["bash", "-n", abs_path])
            results.append(f"bash -n（语法检查）：\n{out2}" if out2 else "bash -n：语法正常")

    else:
        results.append(f"不支持该语言的诊断工具：{lang or '未知'}（后缀：{Path(file_path).suffix}）")

    return "\n\n".join(results) if results else "(无诊断输出)"


# ── AskUserQuestion 实现 ────────────────────────────────────────

def _ask_user_question(
    question: str,
    options: list[dict] | None = None,
    allow_freetext: bool = True,
) -> str:
    """
    阻塞代理循环并在终端向用户显示问题。

    REPL 循环（pycc.py）定期调用 drain_pending_questions()
    渲染问题并收集答案。使用 threading.Event 阻塞直到用户回复。
    """
    event = threading.Event()
    result_holder: list[str] = []
    entry = {
        "question": question,
        "options": options or [],
        "allow_freetext": allow_freetext,
        "event": event,
        "result": result_holder,
    }
    with _ask_lock:
        _pending_questions.append(entry)

    # 阻塞直到用户回答
    event.wait(timeout=300)  # 最长等待 5 分钟

    if result_holder:
        return result_holder[0]
    return "(未回答 — 超时)"


def ask_input_interactive(prompt: str, config: dict, menu_text: str = None) -> str:
    """提示用户输入。如果提供 menu_text，会先打印。"""
    if menu_text:
        print(menu_text)
    try:
        return input(prompt)
    except (KeyboardInterrupt, EOFError):
        print()
        return ""

def drain_pending_questions(config: dict) -> bool:
    """
    由 REPL 循环在每次流式响应后调用。
    渲染待处理问题并收集用户输入。
    如果有回答返回 True。
    """
    with _ask_lock:
        pending = list(_pending_questions)
        _pending_questions.clear()

    if not pending:
        return False

    for entry in pending:
        question = entry["question"]
        options  = entry["options"]
        allow_ft = entry["allow_freetext"]
        event    = entry["event"]
        result   = entry["result"]

        print()
        print("\033[1;35m❓ 助手提问：\033[0m")
        print(f"   {question}")

        if options:
            print()
            for i, opt in enumerate(options, 1):
                label = opt.get("label", "")
                desc  = opt.get("description", "")
                line  = f"  [{i}] {label}"
                if desc:
                    line += f" — {desc}"
                print(line)
            if allow_ft:
                print("  [0] 输入自定义答案")
            print()

            while True:
                raw = ask_input_interactive("你的选择（数字或文本）：", config).strip()
                if not raw:
                    break

                if raw.isdigit():
                    idx = int(raw)
                    if 1 <= idx <= len(options):
                        raw = options[idx - 1]["label"]
                        break
                    elif idx == 0 and allow_ft:
                        raw = ask_input_interactive("你的答案：", config).strip()
                        break
                    else:
                        print(f"无效选项：{idx}")
                        raw = ""
                        continue
                elif allow_ft:
                    break
        else:
            print()
            raw = ask_input_interactive("你的答案：", config).strip()

        result.append(raw)
        event.set()

    return True


def _sleeptimer(seconds: int, config: dict) -> str:
    import threading
    cb = config.get("_run_query_callback")
    if not cb:
        return "错误：内部回调缺失，pycc 未提供 _run_query_callback"
        
    def worker():
        import time
        time.sleep(seconds)
        cb("(系统自动事件)：计时器已完成。请唤醒并执行监控任务。")
        
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    return f"已成功设置 {seconds} 秒计时器。你可以输出总结并结束回合，到时会自动唤醒。"


# ── 调度器（兼容旧版）──────────────────────────────────────

def execute_tool(
    name: str,
    inputs: dict,
    permission_mode: str = "auto",
    ask_permission: Optional[Callable[[str], bool]] = None,
    config: dict = None,
    tool_use_id: Optional[str] = None,
) -> str:
    """调度工具执行；对写入/删除操作请求权限。

    权限检查在此完成，然后委托给注册中心。
    config 字典会传递给工具，用于访问运行时上下文。
    """
    cfg = config or {}

    def _check(desc: str) -> bool:
        """返回是否允许操作。"""
        if permission_mode == "accept-all":
            return True
        if ask_permission:
            return ask_permission(desc)
        return True  # 无界面模式：允许所有操作

    # --- 权限检查 ---
    if name == "Write":
        if not _check(f"写入 {inputs['file_path']}"):
            return "已拒绝：用户取消写入操作"
    elif name == "Edit":
        if not _check(f"编辑 {inputs['file_path']}"):
            return "已拒绝：用户取消编辑操作"
    elif name == "Bash":
        cmd = inputs["command"]
        if permission_mode != "accept-all" and not _is_safe_bash(cmd):
            if not _check(f"执行命令：{cmd}"):
                return "已拒绝：用户取消命令执行"
    elif name == "NotebookEdit":
        if not _check(f"编辑笔记本 {inputs['notebook_path']}"):
            return "已拒绝：用户取消笔记本编辑"

    return _registry_execute(name, inputs, cfg, tool_use_id=tool_use_id)


# ── 注册内置工具到插件中心 ──────────────────────────────

def _register_builtins() -> None:
    """注册所有内置工具到中央注册中心。"""
    _schemas = {s["name"]: s for s in TOOL_SCHEMAS}

    _tool_defs = [
        ToolDef(
            name="Read",
            schema=_schemas["Read"],
            func=lambda p, c: _read(**p),
            read_only=True,
            concurrent_safe=True,
        ),
        ToolDef(
            name="Write",
            schema=_schemas["Write"],
            func=lambda p, c: _write(**p),
            read_only=False,
            concurrent_safe=False,
        ),
        ToolDef(
            name="Edit",
            schema=_schemas["Edit"],
            func=lambda p, c: _edit(**p),
            read_only=False,
            concurrent_safe=False,
        ),
        ToolDef(
            name="Bash",
            schema=_schemas["Bash"],
            func=lambda p, c: _bash(p["command"], p.get("timeout", 30)),
            read_only=False,
            concurrent_safe=False,
        ),
        ToolDef(
            name="Glob",
            schema=_schemas["Glob"],
            func=lambda p, c: _glob(p["pattern"], p.get("path")),
            read_only=True,
            concurrent_safe=True,
        ),
        ToolDef(
            name="Grep",
            schema=_schemas["Grep"],
            func=lambda p, c: _grep(
                p["pattern"], p.get("path"), p.get("glob"),
                p.get("output_mode", "files_with_matches"),
                p.get("case_insensitive", False),
                p.get("context", 0),
            ),
            read_only=True,
            concurrent_safe=True,
        ),
        ToolDef(
            name="WebFetch",
            schema=_schemas["WebFetch"],
            func=lambda p, c: _webfetch(p["url"], p.get("prompt")),
            read_only=True,
            concurrent_safe=True,
        ),
        ToolDef(
            name="WebSearch",
            schema=_schemas["WebSearch"],
            func=lambda p, c: _websearch(p["query"]),
            read_only=True,
            concurrent_safe=True,
        ),
        ToolDef(
            name="NotebookEdit",
            schema=_schemas["NotebookEdit"],
            func=lambda p, c: _notebook_edit(
                p["notebook_path"],
                p["new_source"],
                p.get("cell_id"),
                p.get("cell_type"),
                p.get("edit_mode", "replace"),
            ),
            read_only=False,
            concurrent_safe=False,
        ),
        ToolDef(
            name="GetDiagnostics",
            schema=_schemas["GetDiagnostics"],
            func=lambda p, c: _get_diagnostics(
                p["file_path"],
                p.get("language"),
            ),
            read_only=True,
            concurrent_safe=True,
        ),
        ToolDef(
            name="AskUserQuestion",
            schema=_schemas["AskUserQuestion"],
            func=lambda p, c: _ask_user_question(
                p["question"],
                p.get("options"),
                p.get("allow_freetext", True),
            ),
            read_only=True,
            concurrent_safe=False,
        ),
        ToolDef(
            name="SleepTimer",
            schema=_schemas["SleepTimer"],
            func=lambda p, c: _sleeptimer(p["seconds"], c),
            read_only=False,
            concurrent_safe=True,
        ),
    ]
    for td in _tool_defs:
        register_tool(td)


_register_builtins()


# ── 记忆工具 ────────────────────────────────────────────────
# 定义在 memory/tools.py，导入即自动注册。
import memory.tools as _memory_tools  # noqa: F401


# ── 多代理工具 ───────────────────────────────────────────
# 定义在 multi_agent/tools.py，导入即自动注册。
import multi_agent.tools as _multiagent_tools  # noqa: F401

# 导出以兼容旧版
from multi_agent.tools import get_agent_manager as _get_agent_manager  # noqa: F401


# ── 技能工具 ────────────────────────────────────────────────
# 定义在 skill/tools.py，导入即自动注册。
import skill.tools as _skill_tools  # noqa: F401


# ── MCP 工具 ─────────────────────────────────────────────────────────────────
# mcp/tools.py 连接配置的 MCP 服务器并注册工具。
# 在后台线程连接，不阻塞启动。
import mcp.tools as _mcp_tools  # noqa: F401


# ── 任务工具 ─────────────────────────────────────────────────────
# task/tools.py 导入时自动注册。
import task.tools as _task_tools  # noqa: F401


# ── 计划模式工具（EnterPlanMode / ExitPlanMode）─────────────────────────

def _enter_plan_mode(params: dict, config: dict) -> str:
    """激活计划限制层（Planning Overlay）：仅计划文件可写。"""
    from plan_mode import enter_plan_mode
    task_desc = params.get("task_description", "")
    message, _ = enter_plan_mode(config, task_desc)
    return message


def _exit_plan_mode(params: dict, config: dict) -> str:
    """停用计划限制层并提交计划供用户审核。"""
    from plan_mode import exit_plan_mode
    message, _ = exit_plan_mode(config, require_nonempty=True)
    return message


_PLAN_MODE_SCHEMAS = [
    {
        "name": "EnterPlanMode",
        "description": (
            "激活计划限制层（Planning Overlay）：分析项目并编写执行计划。"
            "适用于复杂、多文件任务。"
            "不会改变基础 permission_mode，仅叠加只读规划约束："
            "仅计划文件可写，其他文件写入会被拦截。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "task_description": {
                    "type": "string",
                    "description": "任务描述",
                },
            },
            "required": [],
        },
    },
    {
        "name": "ExitPlanMode",
        "description": (
            "停用计划限制层并提交计划供用户批准。"
            "写入计划后调用此工具。"
            "停用后基础 permission_mode 保持不变；必须获得用户批准才能开始实现。"
        ),
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]

register_tool(ToolDef(
    name="EnterPlanMode",
    schema=_PLAN_MODE_SCHEMAS[0],
    func=_enter_plan_mode,
    read_only=False,
    concurrent_safe=False,
))

register_tool(ToolDef(
    name="ExitPlanMode",
    schema=_PLAN_MODE_SCHEMAS[1],
    func=_exit_plan_mode,
    read_only=False,
    concurrent_safe=False,
))