---
name: long-mission
description: 仅当用户显式输入 /long-mission 启动无人值守的长期任务时使用；绝不自动调用。
disable-model-invocation: true
argument-hint: "[任务简述]"
hooks:
  Stop:
    - hooks:
        - type: command
          command: 'python3 "$HOME/.claude/skills/long-mission/scripts/stop_hook.py"'
---

# long-mission：无人值守长期任务

把一个大目标变成可无人值守运行的长任务：**先一次性问清所有需要用户决定的事并冻结合同**，然后由控制器循环执行「观察 → 诊断 → 决策 → 派工 → 独立验收 → 记录」，由确定性 governor 判定成功 / 停滞 / 预算耗尽 / 阻塞，Stop hook 在每轮结束时把会话拉回循环，直到 governor 给出终止裁决。

**只在用户输入 `/long-mission` 时运行。** 不要因为任务“看起来很大”就自动套用本流程。用户没有显式调用时，一句话说明本 skill 需要 `/long-mission` 启动即可。

## 硬规则

违反任何一条都算任务失败。

1. **Phase 0 之后不再向用户提问、请求确认、或要求用户做任何操作。** 包括 AskUserQuestion、正文里的“请告诉我”、等待批准。
2. **没有证据 = 没完成。** 口头声明、worker 自评、“应该可以”都不算证据。
3. **worker 不自验，控制器不验收自己。** 强制验收项必须由独立 evaluator 子 agent 实际运行检查后给出 PASS。
4. **governor 是硬门禁。** 每轮先跑 governor；`verdict != CONTINUE` 时立即进入终止流程，不得继续派工。
5. **合同冻结。** 执行中只能改计划（L1/L2），不能改目标、强制验收标准、预算或权限（L3）；L3 只能由用户在任务开始前授权。
6. **一切状态落盘。** `.mission/` 是唯一事实来源；上下文被压缩或会话重启后从磁盘恢复，不靠记忆。
7. **急停开关是 `.mission/AGENT_STOP`。** 用户创建该文件后，下一次 governor 检查必须返回 ABORTED 并停止。

## 五层角色

| 层 | 由谁执行 | 职责 |
|---|---|---|
| 合同 Contract | 用户 + Phase 0 | 目标、强制验收、约束、预算、默认决策。**冻结** |
| Governor | `scripts/governor.py` | 确定性判定 CONTINUE / SUCCESS / STAGNATED / BLOCKED / BUDGET_EXHAUSTED / IMPOSSIBLE / ABORTED |
| Controller | 你（主会话） | 观察、诊断、决策、派工、记录；不亲自做重活，不给自己打分 |
| Planner | `PLAN.md` | 可变、有版本；L1/L2 调整 |
| Workers / Evaluator | 子 agent | Worker 执行并留证据；Evaluator 独立复跑检查、只给裁决 |

## Phase 0 — Preflight（唯一允许提问的阶段）

目标：在开始前把所有需要用户决定的信息一次问清，之后不再打扰。

### 0.1 环境预检（先做，不问用户）

```bash
pwd
test -w . && echo "writable"
test -f "$HOME/.claude/skills/long-mission/scripts/stop_hook.py" && echo "hook ok"
python3 "$HOME/.claude/skills/long-mission/scripts/governor.py" --self-test
git rev-parse --is-inside-work-tree 2>/dev/null || echo "no git"
```

- 如果不在 auto 权限模式、或权限 allowlist 会导致执行中弹确认：**不要开始**。告诉用户需要先切到 auto mode / 配好允许项，因为无人值守期间无法点确认。
- 如果 `.mission/state.json` 已存在：这是已有任务。问用户「继续原任务，还是归档后新建？」（这是调用时提问，允许）。选择新建时，先把旧 `.mission/` 整体移到 `.mission-archive/<YYYYMMDD-HHMM>/` 保留审计，再初始化；不要删除旧目录。若旧任务已是终止态，说明直接 resume 会再次终止（见“恢复与异常”），让用户选择归档新建或授权 L3 重置。
- 如果 governor 自检失败：不要开始，报告失败输出。

### 0.2 一次性访谈

用 `AskUserQuestion`（每次最多 4 个问题，最多 2–3 轮）或一条结构化消息，问清：

1. **目标与完成定义**：最终交付物是什么？怎样算完成？
2. **验收标准**：每条必须能机械验证（命令 + 期望输出 / 产物 + 检查方式）。至少一条强制项。
3. **约束**：不能改的文件/目录、不能用的方法、必须遵守的规范。
4. **预算**：最长墙钟小时数、最大迭代轮数；费用上限可选（无法读取费用时留空）。
5. **权限**：允许安装依赖？联网？git commit / push？删除或覆盖文件？启动服务？
6. **默认决策**：歧义时按什么原则选？哪些决定必须留给你？
7. **中止与保底**：什么情况算 abort？最低可接受成果（PARTIAL）是什么？
8. **可用性**：预计离开多久；回来后在哪个文件看报告。

用户说“你定”时，给出默认值并让用户确认；不要默默替用户决定。

### 0.3 写合同草案并请求唯一一次批准

把答案整理成合同草案（用 `${CLAUDE_SKILL_DIR}/templates/CONTRACT.md`），同时给出里程碑大纲。展示给用户，然后请求 **一次** 批准：

> 回复“批准”开始；要改哪条直接说。批准后我不再打扰你，直到任务结束或触发急停。

- 用户没有明确批准前，**不得创建 `.mission/state.json`、不得开始执行**。
- 用户书面说“全部交给你、不用再问”时，把这句话原文记入合同，视为对该合同的批准；仍然要先展示合同草案。
- 批准后，合同冻结。任何 L3 变更只能留到结束报告里提出。
- 同时告诉用户急停方式：在项目根目录执行 `touch .mission/AGENT_STOP`，下一次 governor 检查会返回 ABORTED 并停止；只想暂停循环则 `touch .mission/PAUSE`，恢复时删除该文件并重新进入任务。

## Phase 1 — Mission setup

**只对新建任务执行本阶段。** 若 `.mission/state.json` 已存在，这是恢复：跳过本阶段，不要覆盖任何已有文件，直接进入 Phase 2。

```bash
mkdir -p .mission/EVIDENCE .mission/worktrees
cp "${CLAUDE_SKILL_DIR}/templates/CONTRACT.md" .mission/CONTRACT.md
cp "${CLAUDE_SKILL_DIR}/templates/PLAN.md" .mission/PLAN.md
cp "${CLAUDE_SKILL_DIR}/templates/PROGRESS.md" .mission/PROGRESS.md
cp "${CLAUDE_SKILL_DIR}/templates/DECISIONS.md" .mission/DECISIONS.md
cp "${CLAUDE_SKILL_DIR}/templates/FAILED.md" .mission/FAILED.md
cp "${CLAUDE_SKILL_DIR}/templates/BLOCKERS.md" .mission/BLOCKERS.md
cp "${CLAUDE_SKILL_DIR}/templates/REPORT.md" .mission/REPORT.md
cp "${CLAUDE_SKILL_DIR}/templates/state.json" .mission/state.json
```

填写要求：

- `CONTRACT.md`：目标、验收表（ID / 是否强制 / 检查命令 / 期望）、约束、允许/禁止动作、默认决策、预算、保底成果、abort 条件、批准原文与时间。
- `PLAN.md`：`# Plan v1`，每个里程碑写明验收 ID、步骤、验证命令、风险、回退方案、是否先做探索。里程碑必须能映射到合同验收项。
- `state.json`：
  - `started_at` 用当前 UTC 时间（ISO8601）。
  - `acceptance` 与合同验收表一一对应；`check` 写可执行命令或可机械核对的说明；每项带 `evaluator_verdict`（初始 `null`）。
  - `final_verification` 带 `evaluator_verdict` 与 `evidence`（初始 `null` / 空数组）。
  - `partial_success.minimum_viable_criteria` 填保底验收 ID。
  - `budget` / `limits` 按 Phase 0 填写；默认 `no_progress_limit=5`、`same_failure_limit=3`、`critical_blocker_limit=3`、`max_replans=6`。
  - `safety.stop_file` 保持 `.mission/AGENT_STOP`。
- 记录基线：运行现有检查，把原始输出存到 `.mission/EVIDENCE/baseline/`，并在 `PROGRESS.md` 写一行基线结论。
- 如果是 git 仓库且用户允许：提交一次 `mission: initialize <id>`；之后每个有进展的循环提交一次。

### Stop hook 自检（只做一次，趁用户还在）

Phase 1 完成后，先对用户说：

> 现在做一次 Stop-hook 自检：我会结束这一轮。若几秒内没有自动继续，说明 hook 没生效，请不要离开；若自动继续，说明无人值守循环可用。

然后结束本轮。Stop hook 应阻止结束并注入“Mission still active”。恢复后：

- 在 `PROGRESS.md` 记录 `hook: ok`，继续 Phase 2。
- 如果没有自动恢复：不要假装成功；会话已经停下，用户能看到。等用户回来后修复 hook 再继续。

## Phase 2 — 自主控制循环

**每一轮都从 governor 开始。** 在项目根目录运行：

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/governor.py" --state .mission/state.json
```

解析 stdout JSON：

- `verdict == "CONTINUE"`：继续本轮。
- 其它任何 verdict：立即进入 Phase 3，按 `recommended_outcome` 收尾，不再派工。

一轮的固定顺序：

### 1. OBSERVE

```bash
tail -n 100 .mission/PROGRESS.md
cat .mission/state.json
cat .mission/BLOCKERS.md
ls -t .mission/EVIDENCE | head
git status --short 2>/dev/null || true
```

只看需要的东西，避免把大量日志读进上下文。详细输出留在 `EVIDENCE/`。

### 2. DIAGNOSE

给上一轮归类，只能选一类：

| 现象 | 诊断 |
|---|---|
| 检查失败，原因明确、可局部修 | implementation bug |
| 同一方法修过 2 次仍同类失败 | approach wrong |
| 验收标准无法验证、步骤与验收脱节 | plan/spec defect |
| 缺工具、缺权限、网络/服务不可用 | environment |
| evaluator 与 worker 结论冲突 | evaluator problem |
| 目标自相矛盾、约束不可能同时满足 | contract problem |

### 3. DECIDE

本轮只做一个决定，并同步更新计数器：

| 决定 | 含义 | 计数器 |
|---|---|---|
| `CONTINUE` | 按计划做下一步 | — |
| `ADJUST` | 同计划内换实现细节（L1） | 失败则 `same_failure_count++` |
| `REPLAN` | 计划/方法有缺陷，升 PLAN 版本（L2） | `replan_count++`，写 `FAILED.md` |
| `EXPLORE` | 开 2–3 个候选方案并行试 | 见下文探索组合 |
| `SKIP` | 非关键项，记录后跳过 | 写 `BLOCKERS.md` |
| `DEFER` | 需要用户的不可逆决定，先做别的 | 写 `BLOCKERS.md`，关键路径则 `critical_blocker_attempts++` |

如果所有剩余关键工作都只能 DEFER/SKIP（都需要用户决定），把 `state.json` 的 `status` 设为 `"blocked"`，写清 `BLOCKERS.md`，然后进入终止流程；不要空转到 STAGNATED。

“有进展”只指：新的验收项通过且有证据、阻塞被解除、里程碑完成。Worker 写了代码但检查没通过 **不算** 进展，`consecutive_no_progress++`。出现新的错误信息算新诊断信息，可重置 `consecutive_no_progress`，并把 `same_failure_count` 设为 1。

### 4. ACT

派 worker 子 agent 执行一个可验证的小步。用 `${CLAUDE_SKILL_DIR}/templates/WORKER_BRIEF.md` 生成 brief：

- 一个 worker 只做一步；写明允许修改/禁止修改的路径、验收命令、证据保存位置。
- 要求 worker **实际运行**验收命令，把原始输出写入 `.mission/EVIDENCE/<step-id>/`，返回不超过 20 行摘要。
- worker 不得修改 `CONTRACT.md`、`state.json`、检查脚本，不得询问用户，不得自评完成。
- 多个 worker 只有在写入范围完全不重叠时才能并行；否则串行。
- 控制器自己不做重活；控制器的时间用于判断和记录。

### 5. VERIFY

- 小步的机械检查可以由控制器跑；**任何强制验收项要标记 `passed` 前，必须由独立 evaluator 给出 PASS。**
- 用 `subagent_type: "Explore"`（只读工具集）派 evaluator，提示词用 `${CLAUDE_SKILL_DIR}/templates/EVALUATOR_BRIEF.md`。evaluator 必须自己运行检查，不能采信 worker 摘要。
- evaluator 裁决只有四种：`PASS` / `NEEDS_WORK` / `IMPOSSIBLE` / `SPEC_PROBLEM`。
- 只有 `PASS` 才能把 `passed` 设为 `true`，同时写入 `evaluator_verdict: "PASS"` 和 evaluator 指出的证据路径。`passed`、`evidence`、`evaluator_verdict` 三者缺一，governor 不会承认完成。
- evaluator 没有写权限；控制器把 evaluator 的原始裁决（命令、输出、证据路径）转写到 `.mission/EVIDENCE/<id>/evaluator-<n>.md`，再更新 `state.json`。
- evaluator 说 `NEEDS_WORK` 时，即使你认为它错了，也不得直接改成 PASS；派第二个 evaluator 复核，两次结论都记录。
- `SPEC_PROBLEM` 说明验收标准本身有问题：按 L2 修计划；若必须改合同，走 L3（停止或留待用户）。

### 6. RECORD

每轮结束前必须落盘：

- `state.json`：`updated_at`、`counters`、`acceptance`（`passed` + `evidence` + `evaluator_verdict`）、`final_verification`（只在真正做完最终验证时改）。
- `PROGRESS.md`：追加一个 cycle 块（见模板）。
- 有判断 → `DECISIONS.md`；有死路 → `FAILED.md`；有阻塞/延期 → `BLOCKERS.md`。条目用模板里的 `D-xxx` / `F-xxx` / `B-xxx` 编号，最终报告引用编号，避免重复全文。
- 有 git 且允许 → 提交一次，提交信息写清 milestone/step。
- 更新 `PLAN.md` 顶部的当前里程碑与版本（L2 才升版本）。

### 7. LOOP

回到第 1 步。governor 仍为 CONTINUE 就不要主动结束回合；即使因为上下文或工具限制必须让出，Stop hook 会用同样的话把你拉回来。

### 上下文纪律

- 控制器上下文只保留：合同摘要、当前里程碑、最近一轮结果、未解阻塞。
- 大输出一律写文件，不读回全文；需要时只读关键行。
- 上下文被压缩后，先读 `PROGRESS.md` 最后 100 行 + `state.json` + `CONTRACT.md`，再跑 governor，然后继续。**不要重新访谈。**

## 反馈与重规划

### L1 — ADJUST

同一步换实现、换参数、换命令。不升计划版本，写 `PROGRESS.md` 即可。

### L2 — REPLAN

里程碑方法、步骤顺序、验收映射需要改。必须：

1. 在 `PROGRESS.md` 写诊断和证据；
2. 在 `FAILED.md` 记录被放弃的方法及失败证据；
3. `PLAN.md` 升版本（`# Plan v2`），写变更日志；
4. `state.json` 的 `replan_count++`；
5. 不得降低合同验收标准；只能改“怎么做”，不能改“做到什么”。

同一里程碑最多 3 次 L2；全局超过 `max_replans` 由 governor 判 STAGNATED。

### L3 — 合同变更

改目标、强制验收、预算、权限、允许动作。**无人值守期间禁止。** 只能写进 `BLOCKERS.md` 和最终报告，等用户回来决定。除非用户在 Phase 0 的合同里写明了具体的自动变更规则，才可按规则执行并记录。

### 探索组合（EXPLORE）

触发条件（满足其一）：

- 方法不确定，存在 2–3 个看似可行的方案；
- 同一方法连续失败 2 次；
- 选错代价高（返工大、影响后续里程碑）。

做法：

1. 每个候选一个隔离 worktree / 分支（无 git 时复制目录到 `.mission/worktrees/<name>`）；
2. 所有候选拿到相同的 brief 和固定预算（例如各 1 轮 / 30 分钟）；
3. 完成后由 evaluator 盲评（不告诉它哪个是“主方案”）；
4. 按验收结果、简单性、风险打分，保留一个，其余写 `FAILED.md`；
5. 每个里程碑最多一次探索组合，探索总预算不超过总预算 30%。

## 不打断规则

Phase 0 之后，任何情况下都不要向用户提问或等待操作。遇到需要判断的事，按以下顺序：

1. `CONTRACT.md` 有明确规定 → 照做。
2. `DECISIONS.md` 有先例 → 沿用。
3. 可逆、低风险 → 选最保守的方案，记 `review-at-end`。
4. 可逆但影响较大 → 选保留最多后续选项的方案，记录理由。
5. **不可逆 / 高风险 / 对外动作**（push、部署、发布、发消息、花钱、删数据）→ **不做**，写 `BLOCKERS.md` 后去做别的。
6. 关键路径被第 5 类卡住 → `critical_blocker_attempts++`；达到上限由 governor 判 BLOCKED。

被工具权限拒绝时：不要原样重试；记录拒绝信息，换允许的路径。**不要用别的工具绕开同一个拒绝意图**（换的是达成目标的方法，不是绕过权限）。这条同样适用于工具策略守卫（例如 report-file guard、子 agent 写限制）：不要用 Bash 重定向/heredoc 绕过，按对应兜底处理。auto mode 分类器临时限流时，`sleep 10` 后重试一次；连续失败按 environment blocker 处理。

**不要在正文里写“请告诉我”“等你确认”。** 有疑问就写进 `BLOCKERS.md` 的 `Question` / `Default taken` / `Impact if wrong` 三栏，然后继续。

## Phase 3 — 终止与报告

governor 返回非 CONTINUE 后：

1. **不要再派工。** 停止所有新动作。
2. 如果是 SUCCESS 候选（所有强制项已通过、有证据、且有独立 evaluator PASS），做最终验证：
   - 从干净状态运行全部强制验收命令；
   - 运行回归检查；
   - 检查禁止修改的文件未被改动、无调试残留、`git status` 已审阅；
   - 派独立 evaluator 做最终审计；
   - 全部通过后，才设置 `final_verification.passed / clean_state / no_regressions = true`，同时写 `final_verification.evaluator_verdict = "PASS"` 和最终审计证据路径，再跑一次 governor 确认 SUCCESS。
   - **不要在最终验证前设置这些字段。**
3. 按 governor 的 verdict 与 `recommended_outcome` 填 `.mission/REPORT.md`：
   - 结果（SUCCESS / PARTIAL / FAILED / BLOCKED / ABORTED / BUDGET_EXHAUSTED / STAGNATED / IMPOSSIBLE）；
   - 完成了什么、证据索引；
   - 没完成什么、为什么；
   - 关键决策、失败方法、需要用户处理的事项；
   - 如何恢复/继续。
4. 给用户的最终消息：简短，说明结果、报告路径、需要用户做的事。不要夸大；PARTIAL 就说 PARTIAL。
5. 如果 `Write` 被策略拒绝（例如控制器跑在子 agent 里，存在 report-file guard），**不要用 Bash heredoc / 重定向 / 其他工具绕过**；把完整报告作为最终消息返回给调用方，由有权限的会话落盘，并在 `PROGRESS.md` / `state.json` 里注明报告未落盘及原因。
6. 保留 `.mission/` 供审计。停在终止 verdict 上，Stop hook 会允许结束。

### 结果含义

| 结果 | 含义 |
|---|---|
| `SUCCESS` | 所有强制验收通过、证据齐全、独立 evaluator PASS、最终验证通过 |
| `PARTIAL` | 达到保底成果，但未完成全部强制项 |
| `FAILED` | 未达到保底成果 |
| `BLOCKED` | 关键路径被外部条件卡住，重试到上限 |
| `ABORTED` | 用户急停或检测到越权动作 |
| `BUDGET_EXHAUSTED` | 时间/轮数/费用到上限 |
| `STAGNATED` | 连续无进展、同一失败重复、或重规划过多 |
| `IMPOSSIBLE` | 有证据表明合同约束下不可达 |

## 已知限制（开始前应让用户知道）

| 限制 | 影响 | 缓解 |
|---|---|---|
| 循环依赖当前会话与 Stop hook | 编辑器关闭、进程崩溃、API 长时间失败时任务会停 | 重新 `/long-mission` 选择“继续”，从 `.mission/` 恢复 |
| 无法绕过权限确认 | 非 auto mode 下会卡住 | Phase 0 预检；不满足就不开始 |
| 无法执行需要用户授权的 L3 变更 | 目标/验收/预算/权限不能中途改 | 写入 `BLOCKERS.md`，以 PARTIAL/BLOCKED 收尾 |
| 费用无法精确读取 | `max_cost_usd` 可能不生效 | 以 `max_hours` / `max_iterations` 为主预算 |
| `max_hours` 是墙钟时间 | 机器休眠/暂停也计入 | 预算留足余量，或用 `PAUSE` 前先记时间 |
| evaluator 仍是 LLM | 可能误判 | 强制它实际运行命令、留原始输出；争议时二次复核 |
| 2.1.278 的 skill hook 需运行时验证 | hook 未注册时循环不会自动继续 | Phase 1 的 Stop-hook 自检；失败就不要无人值守 |

## 恢复与异常

- **上下文压缩 / 会话重启**：读 `.mission/PROGRESS.md` 最后 100 行、`state.json`、`CONTRACT.md`、最新 `PLAN.md`，跑 governor，继续。不要重新访谈、不要重建任务。
- **用户重新 `/long-mission` 且已有 `.mission/`**：Phase 0 问“继续还是归档新建”；继续则跳过访谈和 Phase 1 设置，直接恢复循环。新会话恢复时，先删除 `state.json` 里的 `session_id` 字段，让 Stop hook 重新绑定当前会话；否则 hook 会把它当成别的会话而放行停止。
- **已有任务是终止态（计数到限 / status 为 blocked、impossible、aborted）**：直接 resume 会立刻再次终止。告诉用户两个选项：修复外部条件后归档旧 `.mission/` 并新建任务，或由用户明确授权 L3 重置 counters/limits。**不要自行清零 counters。**
- **`.mission/` 缺失或损坏**：不要猜；停止并报告缺了哪个文件。
- **hook 没生效**：会话会直接停下；用户在场时能看到。修复后再继续，不要假装无人值守可用。

## 红旗清单（出现即停手纠正）

- 正准备向用户提问 / 请求确认 / 等批准 → Phase 0 之后禁止。
- 准备把验收项设为 `passed` 但没有 evaluator PASS 和证据 → 禁止。
- 准备让 worker 自己说“完成了”就算完成 → 禁止。
- 准备修改合同、预算、强制验收 → L3，禁止。
- 同一失败准备第 3 次原样重试 → 先诊断，再决定 ADJUST/REPLAN。
- 准备因为“测试看起来绿了”宣布成功 → 先做最终验证。
- 准备在没有 governor 裁决的情况下停止 → 禁止。
- 准备执行 push / 部署 / 发布 / 发消息 / 删数据 → 除非合同明确授权，否则 DEFER。
- 准备删除或重写 `.mission/` → 禁止。

## 文件

| 路径 | 用途 |
|---|---|
| `.mission/CONTRACT.md` | 冻结合同 |
| `.mission/PLAN.md` | 可变计划，带版本 |
| `.mission/state.json` | governor 与控制器共享状态 |
| `.mission/PROGRESS.md` | 每轮进度 |
| `.mission/DECISIONS.md` | 自主决策记录 |
| `.mission/FAILED.md` | 失败方法与证据 |
| `.mission/BLOCKERS.md` | 阻塞、延期、待用户事项 |
| `.mission/EVIDENCE/` | 原始命令输出与验收证据 |
| `.mission/REPORT.md` | 最终报告 |
| `.mission/AGENT_STOP` | 用户急停文件（创建即 ABORTED） |
| `.mission/PAUSE` | 用户暂停文件（Stop hook 放行；删除后恢复） |
| `scripts/governor.py` | 确定性终止判定 |
| `scripts/stop_hook.py` | Stop hook，维持循环 |
| `templates/` | 合同、计划、状态、台账、worker/evaluator 模板 |
