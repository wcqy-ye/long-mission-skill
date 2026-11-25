# long-mission

Claude Code 的无人值守长期任务 skill：先一次性问清所有需要用户决定的事并冻结合同，然后由控制器循环执行「观察 → 诊断 → 决策 → 派工 → 独立验收 → 记录」，由确定性 governor 判定成功 / 停滞 / 预算耗尽 / 阻塞，Stop hook 在每轮结束时把会话拉回循环，直到给出终止裁决。

**只在用户显式输入 `/long-mission` 时运行**，不会因为任务“看起来很大”自动触发。

## 需要什么

- Claude Code（本 skill 在 **2.1.278** / WSL2 下验证；skill 级 Stop hook 需要该版本支持）
- Python 3（仅标准库，无第三方依赖）
- **auto 权限模式**：无人值守期间无法逐条点确认；装好后请确认 `/status` 显示 auto
- 安装路径必须是 `~/.claude/skills/long-mission`，因为 `SKILL.md` 里的 Stop hook 写死了 `python3 "$HOME/.claude/skills/long-mission/scripts/stop_hook.py"`

## 安装

```bash
mkdir -p ~/.claude/skills
cp -r long-mission ~/.claude/skills/
```

验证：

```bash
python3 ~/.claude/skills/long-mission/scripts/governor.py --self-test
```

输出自检通过即可。随后新开一个 Claude Code 会话，输入 `/long-mission <你的目标>`。

卸载：

```bash
rm -rf ~/.claude/skills/long-mission
```

## 使用

```text
/long-mission 用脚本渲染一支 90 秒中文科普短片，输出 MP4+SRT，预算 4 小时
```

Phase 0 会一次性问清目标、验收标准、约束、预算、权限和默认决策，展示合同草案；你回复“批准”后合同冻结，之后直到任务结束或急停都不再打扰你。

### 急停与暂停

在任务项目根目录：

```bash
touch .mission/AGENT_STOP   # 下一次 governor 检查返回 ABORTED 并停止
touch .mission/PAUSE        # 暂时放行 Stop hook；删除该文件后恢复循环
```

任务的全部状态在 `.mission/`：`CONTRACT.md`、`PLAN.md`、`PROGRESS.md`、`DECISIONS.md`、`FAILED.md`、`BLOCKERS.md`、`EVIDENCE/`、`REPORT.md`。上下文被压缩或会话重启后，重新 `/long-mission` 选择“继续”，会从磁盘恢复，不会重新访谈。

## 目录

| 路径 | 用途 |
|---|---|
| `long-mission/SKILL.md` | skill 定义与完整控制流程 |
| `long-mission/scripts/governor.py` | 确定性终止判定（含 `--self-test`） |
| `long-mission/scripts/stop_hook.py` | Stop hook，维持无人值守循环 |
| `long-mission/templates/` | 合同、计划、进度、决策、失败、阻塞、报告、state、worker/evaluator brief |

## 已知限制

- 循环依赖当前 Claude Code 会话与 Stop hook；编辑器关闭、进程崩溃或 API 长时间失败时任务会停，需要重新 `/long-mission` 恢复。
- 不能绕过权限确认；非 auto 模式会卡住。
- 目标、强制验收、预算、权限在批准后冻结（L3），任务中不能改；需要变更只能写进报告，等用户回来处理。
- 费用无法精确读取时，`max_cost_usd` 可能不生效，以 `max_hours` / `max_iterations` 为主预算。
- evaluator 仍是 LLM，可能误判；流程强制它实际运行检查并留原始输出，争议时二次复核。

## 分享

把整个 `long-mission/` 打包即可，接收方解压到 `~/.claude/skills/`：

```bash
tar --exclude='__pycache__' --exclude='*.pyc' -czf long-mission-skill.tar.gz long-mission
```
