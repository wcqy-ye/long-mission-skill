# Mission Contract

> 本文件在 Phase 0 由用户批准后冻结。执行期间不得修改；需要变更时写入 BLOCKERS.md，等用户回来决定。

- **Mission ID**:
- **Created**:
- **Approved at**:
- **Approval quote**（用户批准原文）:

## 1. Goal

（一句话目标）

## 2. Definition of done

（最终交付物；怎样算完成；产物放在哪里）

## 3. Acceptance criteria

每一条必须能机械验证。检查命令要能在项目根目录直接运行。

| ID | Mandatory | Criterion | Check command / method | Expected result |
|---|---|---|---|---|
| A1 | yes | | | |
| A2 | | | | |

## 4. Constraints

### Must

-

### Must not

- 不得修改验收检查脚本（列出具体文件）：
- 不得修改：

## 5. Allowed / forbidden actions

| Action | Allowed? | Notes |
|---|---|---|
| Install dependencies | | |
| Network access | | |
| git commit | | |
| git push | | 默认禁止 |
| Delete / overwrite files | | |
| Start services | | |
| Spend money | | 默认禁止 |
| Outward-facing actions (deploy / publish / send) | | 默认禁止 |

## 6. Decision defaults

歧义时按以下顺序：合同明确规则 → DECISIONS.md 先例 → 可逆低风险选最保守 → 可逆但影响大选保留选项 → 不可逆/高风险不做并记录。

（补充本项目特有的默认值）

## 7. Budget

- `max_hours`（墙钟时间，含暂停）:
- `max_iterations`:
- `max_cost_usd`（可选，读不到就留空）:

## 8. Minimum viable / partial success

达到哪些验收 ID 即算 PARTIAL：

- A

## 9. Abort conditions

- 用户创建 `.mission/AGENT_STOP`
- 检测到未经授权的对外/不可逆动作
- （补充）

## 10. User availability

- 预计离开时长:
- 回来先看: `.mission/REPORT.md`

## 11. Approval

- [ ] 用户已明确批准本合同与里程碑大纲
- 批准原文 / 时间:
