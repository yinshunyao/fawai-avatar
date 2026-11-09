# 按步难度与 Briefly

对照：Cursor 的 `Thought briefly`（折叠短思考 UI）、本仓 [Fast](../功能/Fast/说明.md)、[观察反省](../功能/观察反省/说明.md)、[执行反省](../功能/执行反省/说明.md)、[主脑委派](../功能/主脑委派/说明.md)、[深度思考固定预算](./深度思考固定预算.md)。

---

## 已定方向（本轮确认）

1. **同轮规划字段三档**，与 `actions` / `delegate` 一次写出；**禁止**先规划再另开一轮 LLM 评难度。
2. 三档主要管**本步动作成功后要不要 / 怎样做观察反省**；失败仍走执行反省（与档位解耦，有错就要裁决）。
3. 命名建议（议题落地时可微调，语义锁定）：

| 档（概念） | 字段值（建议） | 成功后 | 规划侧期望 |
|:---|:---|:---|:---|
| 复杂（要反省） | `reflect` | **完整**观察反省 LLM（`advanced` / `stalled` / `enough_to_answer`） | 可开主脑思考；分析、架构、易假成功 |
| 简单（brief） | `brief` | **短观察反省**（仍调 `obs_reflect`，短提示 + 强制关思考；输出宜短） | 短 thought；要确认有没有跑偏 |
| 仅执行 | `act` | **规则检查、不调观察反省 LLM**（`expect` / 退出码 / 非空等；过则 `skipped_act`） | 最短 thought；确定性动作 |

缺省：未写字段时跟现网（机械只读成功仍可机械规则跳过；其余走完整观察反省）。任务 `meta.fast=true` 整单覆盖为跳过观察反省（现状 `skipped_fast`），与步档取「更跳过」即可。

**已定（勿再混）**：`brief` = 短观察反省；**规则检查只属于 `act` 成功路径**，不是 brief 的实现。

---

## 三档与现网闸门

```text
plan() 同轮输出 step_run ∈ {reflect, brief, act} + actions
        ↓
   执行 actions
        ↓
   有失败？ ──是──→ 执行反省（三档都走；不叠观察反省）
        │
        否（本步全成功）
        ↓
   act     → 规则检查（不调观察反省 LLM；不过可升档或当失败）
   brief   → 短观察反省（LLM，关思考，短提示）
   reflect → 完整观察反省
```

| 现网机制 | 与三档关系 |
|:---|:---|
| 机械只读成功跳过 | 并入 / 对齐 `act` 的规则成功路径；模型显式 `act` 时非机械也可走规则检查 |
| 任务 Fast | 整任务强制成功路径跳过 LLM 反省；不取代步字段 |
| 执行反省 | **仅失败**；与三档正交 |
| `delegate` | 委派段内建议默认 `act`/`brief`；主脑验收步用 `reflect` 或 `brief` |

### `brief` = 短观察反省（已定）

- 仍走 `Brain.obs_reflect`（或同入口的 brief 提示变体），**不是**纯规则。
- 强制 `enable_thinking=false`；系统提示收成短裁决（如进度二选一/三选一 + 字数上限）。
- 与完整 `reflect` 共用观察入模材料管道，只减提示深度与思考。

### `act` = 规则检查（已定）

- 成功路径**不调**观察反省 LLM。
- 用 `expect`、退出码、输出非空等确定性条件验收；与动作段预期闸同向。
- 规则不过：升 `brief`/`reflect`，或按失败进执行反省（议题落地时定一条，默认倾向当失败/升档而非静默过）。

---

## 同轮字段：鸡生蛋（思考已花）

本步的 `reflect|brief|act` 是**规划响应里的字段**，无法回溯关掉**这一次**已发生的供应商深度思考。

因此：

- **本步立刻省下的**：`act` 去掉整段观察反省 LLM（只留规则）；`brief` 仍有一次短反省，但比完整 `reflect` 便宜
- **下一规划步**：对 `brief`/`act` 倾向 `enable_thinking=false`（提示词要求自洽：标了 `act`/`brief` 就别长推理）
- **不要**指望「标 act 的那一枪规划」零思考；大头省在成功后的反省路径

---

## 规划 JSON 草样（非放行 API）

```json
{
  "thought": "读配置",
  "step_kind": "act_serial",
  "step_run": "act",
  "status": "continue",
  "actions": [{ "kind": "fs_read", "args": { "path": "…" }, "expect": null }]
}
```

| `step_run` | 何时选 |
|:---|:---|
| `act` | 路径清楚、可用规则验收（ls/读已知文件/带 expect 的命令）；成功＝规则过 |
| `brief` | 要模型瞟一眼结果有没有跑偏（改配置、窄检索）；成功后再打一枪短观察反省 |
| `reflect` | 易假成功、多解、要够用才交、GUI/架构；完整观察反省 |

提示词约束（设想）：执行中 thought 三档都宜短；仅 `reflect` 且临近交稿时允许长终稿（`status=success` 体例另遵完成任务说明）。

---

## 明确否决

- 规划后再 LLM 分析难度（双轮）
- 只用固定 `thinking_budget` 冒充 brief
- 把 `finalize` / 用户终稿默认打成 `act`（无核对的交稿）
- 失败也跳过执行反省

---

## 落地状态

已落地（功能 [步级执行档](../功能/步级执行档/说明.md)）：P0 三档接线 + `act` 规则不过升 `brief` + 短观察反省提示 + facts/规划文案标签 + compose **模式**面板「分档」（`meta.step_tier`，默认关，与极速/全力/计划模式同面板）。

**仍可选（未做）**：`brief`/`act` 步**下一轮规划**默认关思考；`brief` 判 `stalled` 是否硬换道。

---

## 踩坑：`skipped_act` 进度双写 `step_run`

`_step_label_extra(plan)` 在 `plan.step_run` 非空时会往 progress meta 塞 `step_run` / `step_run_label`。`skipped_act` 分支若再写显式 `step_run="act"` 再 `**_step_label_extra`，Python 调用处直接 `TypeError: multiple values for keyword argument 'step_run'`，整 run 崩掉——与工具/SSH/换道无关。纪律：progress 事实字段只走 label_extra（或先拼 dict 再一次 `**`），禁止同名关键字与 spread 并存。问题条：[步级执行档act进度崩溃](../问题/步级执行档act进度崩溃.md)。

---

## 与主脑委派文档

[主脑委派](../功能/主脑委派/说明.md)「不做每步再评估…额外 LLM 轮」仍成立：档位是规划 JSON 的一部分，不是第二轮评分。落地改功能说明时须写明三档字段名与观察反省分支。
