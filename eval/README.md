# pycc SWE-bench Evaluation Harness

对 SWE-bench Lite（300个实例）系统性评测 pycc 在代码理解、多步规划和 bug 修复上的实际能力。

## 文件结构

```
eval/
  run_instance.py   # 单实例运行：clone 仓库 → 调用 pycc → 提取 patch
  batch_eval.py     # 批量运行（多线程），支持断点续跑
  score.py          # 打分：快速启发式 + 官方 Docker 评测
  README.md         # 本文件
```

## 快速开始

### 第一步：环境准备

```bash
pip install swebench datasets
```

官方 Docker 评测还需要：
```bash
# macOS: 安装 Docker Desktop
# Linux: sudo apt install docker.io
```

### 第二步：小规模验证（30个实例）

```bash
# 运行30个实例，2个并行 worker
python eval/batch_eval.py \
    --n 30 \
    --workers 2 \
    --workdir /tmp/pycc_swe_lite \
    --model deepseek/deepseek-v4-pro \
    --timeout 300
```

### 第三步：快速打分（不需要 Docker）

```bash
python eval/score.py --workdir /tmp/pycc_swe_lite
```

输出示例：
```
Results summary
  Total run:    30
  With patch:   28
  No patch:     2
  Errors:       0

── Heuristic estimate (patch-overlap proxy) ──
  Heuristic-resolved: 7 / 30  (23.3%)
```

### 第四步：官方精确评测（需要 Docker）

```bash
# 先确认 Docker 运行正常
docker ps

# 拉取 swebench 官方镜像（一次性，较慢）
python -c "from swebench.harness.docker_build import build_env_images; build_env_images('princeton-nlp/SWE-bench_Lite', max_workers=4)"

# 运行官方评测
python eval/score.py --workdir /tmp/pycc_swe_lite --official
```

### 第五步：全量评测（300个实例）

```bash
python eval/batch_eval.py \
    --workers 3 \
    --workdir /tmp/pycc_swe_full \
    --model deepseek/deepseek-v4-pro \
    --timeout 360
```

预计时间：8-12小时（受 API rate limit 限制）  
预计 API 费用：$20-50（DeepSeek 价格）

## 断点续跑

`batch_eval.py` 默认跳过已有 `result.json` 的实例，直接重跑即可续跑：

```bash
python eval/batch_eval.py --n 30 --workdir /tmp/pycc_swe_lite
```

强制重跑所有实例：

```bash
python eval/batch_eval.py --n 30 --workdir /tmp/pycc_swe_lite --no-skip
```

## 输出目录结构

```
/tmp/pycc_swe_lite/
  astropy__astropy-12907/
    result.json         # 元数据（instance_id, patch, error, elapsed_s）
    candidate.patch     # git diff 输出
    run.log             # pycc 的完整 stdout/stderr
    repo/               # 克隆的仓库（可删除节省空间）
  django__django-11099/
    ...
```

## 打分机制

### 快速启发式（无需 Docker）
检查 candidate.patch 是否修改了 FAIL_TO_PASS 测试覆盖的代码模块。
与真实 resolve rate 约 70% 相关，适合快速迭代。

### 官方评测（需要 Docker）
在隔离 Docker 容器中：
1. 应用 candidate.patch
2. 运行 FAIL_TO_PASS 测试用例
3. 确认 PASS_TO_PASS 测试未退化

这是 SWE-bench 论文中的标准评测方式，产出地面真相 resolve rate。

## 当前 SOTA 参考

| 系统 | SWE-bench Lite resolve rate |
|---|---|
| Claude Opus 4 + 工具链 | ~72% |
| GPT-4o + SWE-agent | ~38% |
| 无训练开源智能体 | ~15-25% |
| **pycc 目标** | **>20%** |
