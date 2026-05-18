"""skill package — reusable prompt templates (skills)."""

# skill 包的对外入口。
# 这里主要做两件事：
# 1. 重新导出 loader / executor 里的公共 API，方便外部统一从 skill 导入。
# 2. 通过导入 builtin 模块触发内置技能注册，这是一个有意保留的副作用。

from .loader import (  # noqa: F401
    SkillDef,
    load_skills,
    find_skill,
    substitute_arguments,
    register_builtin_skill,
    _parse_skill_file,
    _parse_list_field,
)
from .executor import execute_skill  # noqa: F401

# 导入 builtin 时会把内置 skill 写入 loader 里的内置注册表，
# 后续 load_skills() 才能把它们和用户 / 项目技能一起返回。
from . import builtin as _builtin  # noqa: F401
