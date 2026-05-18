"""Skill loading: parse markdown files with YAML frontmatter into SkillDef objects."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class SkillDef:
    # SkillDef 是 skill 系统的核心数据结构。
    # 它把“一个 markdown skill 文件”解析成运行期可消费的统一对象。
    name: str
    description: str
    triggers: list[str]          # ["/commit", "commit changes"]
    tools: list[str]             # ["Bash", "Read"]  (allowed-tools)
    prompt: str                  # full prompt body after frontmatter
    file_path: str
    # 扩展字段
    when_to_use: str = ""        # when Claude should auto-invoke this skill
    argument_hint: str = ""      # e.g. "[branch] [description]"
    arguments: list[str] = field(default_factory=list)  # named arg names
    model: str = ""              # model override
    user_invocable: bool = True  # appears in /skills list
    source: str = "user"         # "user", "project", "builtin"


# skill 的搜索路径。
# 项目级技能优先级最高，用于覆盖用户级或内置 skill。
def _get_skill_paths() -> list[Path]:
    return [
        Path.cwd() / ".pycc" / "skills",   # project-level (priority)
        Path.home() / ".pycc" / "skills",   # user-level
    ]


# frontmatter 里有不少字段支持列表写法，例如：
#   triggers: [/deploy, /ship]
#   allowed-tools: [Bash, Read]
# 这里统一把它们解析成 Python list[str]。
def _parse_list_field(value: str) -> list[str]:
    """Parse YAML-like list: ``[a, b, c]`` or ``"a, b, c"``."""
    value = value.strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    return [item.strip().strip('"').strip("'") for item in value.split(",") if item.strip()]


# 解析单个 skill 文件。
# 约定格式是：
#   --- frontmatter ---
#   <prompt body>
def _parse_skill_file(path: Path, source: str = "user") -> Optional[SkillDef]:
    """Parse a markdown file with ``---`` frontmatter into a SkillDef.

    Frontmatter fields:
        name, description, triggers, tools / allowed-tools,
        when_to_use, argument-hint, arguments, model,
        user-invocable, context
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return None

    if not text.startswith("---"):
        return None

    parts = text.split("---", 2)
    if len(parts) < 3:
        return None

    # parts[1] 是 frontmatter，parts[2] 是真正的 prompt 模板正文。
    frontmatter_raw = parts[1].strip()
    prompt = parts[2].strip()

    fields: dict[str, str] = {}
    for line in frontmatter_raw.splitlines():
        line = line.strip()
        if not line or ":" not in line:
            continue
        key, _, val = line.partition(":")
        fields[key.strip().lower()] = val.strip()

    name = fields.get("name", "")
    if not name:
        return None

    # allowed-tools 的语义比旧字段 tools 更明确，因此优先级更高。
    tools_raw = fields.get("allowed-tools", fields.get("tools", ""))
    tools = _parse_list_field(tools_raw) if tools_raw else []

    triggers_raw = fields.get("triggers", "")
    # 未配置 triggers 时，默认使用 /<name> 作为触发词。
    # 触发器设置，为str 列表
    triggers = _parse_list_field(triggers_raw) if triggers_raw else [f"/{name}"]

    arguments_raw = fields.get("arguments", "")
    arguments = _parse_list_field(arguments_raw) if arguments_raw else []

    user_invocable_raw = fields.get("user-invocable", "true")
    user_invocable = user_invocable_raw.lower() not in ("false", "0", "no")

    return SkillDef(
        name=name,
        description=fields.get("description", ""),
        triggers=triggers,
        tools=tools,
        prompt=prompt,
        file_path=str(path),
        when_to_use=fields.get("when_to_use", ""),
        argument_hint=fields.get("argument-hint", ""),
        arguments=arguments,
        model=fields.get("model", ""),
        user_invocable=user_invocable,
        source=source,
    )


# 内置 skill 注册表。
# builtin.py 导入后会往这里追加 SkillDef。
_BUILTIN_SKILLS: list[SkillDef] = []


def register_builtin_skill(skill: SkillDef) -> None:
    # 这里不做去重，去重在 load_skills() 汇总阶段统一处理。
    _BUILTIN_SKILLS.append(skill)


# 加载所有 skill，并按优先级去重。
def load_skills(include_builtins: bool = True) -> list[SkillDef]:
    """Return skills from disk + builtins, deduplicated (project > user > builtin)."""
    seen: dict[str, SkillDef] = {}

    # 内置 skill 先放进去，作为最低优先级的默认值。
    if include_builtins:
        for sk in _BUILTIN_SKILLS:
            seen[sk.name] = sk

    # reversed 后会先遍历用户级，再遍历项目级；
    # 同名 skill 会被后写入的项目级版本覆盖。
    skill_paths = _get_skill_paths()
    for i, skill_dir in enumerate(reversed(skill_paths)):
        src = "user" if i == 0 else "project"
        if not skill_dir.is_dir():
            continue
        for md_file in sorted(skill_dir.glob("*.md")):
            skill = _parse_skill_file(md_file, source=src)
            if skill:
                seen[skill.name] = skill

    return list(seen.values())


def find_skill(query: str) -> Optional[SkillDef]:
    """Find a skill whose trigger matches the first word (or whole string) of query."""
    query = query.strip()
    if not query:
        return None

    # 这里只看第一段 trigger，是因为用户通常以：
    #   /deploy prod
    # 这种“触发词 + 参数”的形式调用 skill。
    first_word = query.split()[0]
    for skill in load_skills():
        for trigger in skill.triggers:
            if first_word == trigger:
                return skill
            if trigger.startswith(first_word + " "):
                return skill
    return None


# 参数替换：把 skill 模板里的占位符，替换成用户这次传进来的参数
def substitute_arguments(prompt: str, args: str, arg_names: list[str]) -> str:
    """Replace $ARGUMENTS (whole args string) and $ARG_NAME placeholders.

    Named args are positional: first word → first name, etc.
    """
    # $ARGUMENTS 总是替换为完整参数字符串，适合保留原始输入。
    result = prompt.replace("$ARGUMENTS", args)

    # 命名参数是“按位置”映射的，不做复杂 shell 解析。
    # 例如 arguments=[env, version] 时：
    #   /deploy staging 2.1.0
    # 会得到：
    #   $ENV=staging, $VERSION=2.1.0
    arg_values = args.split()
    for i, arg_name in enumerate(arg_names):
        placeholder = f"${arg_name.upper()}"
        value = arg_values[i] if i < len(arg_values) else ""
        result = result.replace(placeholder, value)

    return result
