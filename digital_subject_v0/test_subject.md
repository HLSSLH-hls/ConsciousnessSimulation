# 设计文档（v0）：30秒时间步的“意识转移数字主体”原型
> 由GPT-5.2-high生成。
>
> 目标：在**真实人类时间尺度**（30s/tick）下长期运行数天，展示稳定的“动机—注意—行动—记忆—再动机”的闭环动态。  
> 说明：这是一个**可展示的数字主体原型**（prototype），不是“真实意识证明”，重点是可观察的心理动力学与可扩展的软件结构。

# 注意

-当前版本使用的是deepseek,因此请用户将自己的API_KEY放到代码1233行左右里面的字符串变量中

---

## 1. 总体思路（为什么 v(-1) 会卡死，v0 如何解决）

### v(-1)（原始版本）的结构缺陷
- 状态只有 recent/episodic/todo，缺少：
  - **生理/心理 Need（会随时间上升）**
  - **长期关切 Current Concern（未竟事项牵引）**
  - **注意竞争与工作记忆稀缺**
  - **反卡死机制**（重复行为惩罚、无进展检测）
- TODO 没有进度/next_step，导致“做不出推进感”，模型只能念叨与重复。

### v0 的核心改进
- 引入三层：**Need（驱力）→ Concern（长期关切）→ Attention（竞争选择 top-3）**
- 引入两类保证长期运行的机制：
  1) **确定性动力学**（needs 上升、concerns 激活随停滞上升、tag 触发“物理效果”）  
  2) **反卡死守卫**（重复 tag / 长期无进展 / boredom 上升 → 强制 replan/context_change/recovery）

---

## 2. 系统组件定义

### 2.1 Need（驱力/需要）
**数据结构：**
- `level ∈ [0,1]`：当前强度
- `rise_per_min`：随时间上升速度
- `weight`：重要性权重
- `satisfied_drop`：满足后的下降幅度

**动力学：**
- 每 tick：`level += rise_per_min * dt`
- 行动满足后：`level -= satisfied_drop`（并记录 `last_satisfied_ts`）
- **竞价（bid）**：`weight * level^2`（高位会非线性抢占注意）

> 目的：让主体“拖久了就不得不管”，逼迫行为切换。

---

### 2.2 CurrentConcern（长期关切 / 未竟事项）
采用 Klinger “current concerns” 思路：未完成目标会形成持续激活（tension / preoccupation）。

**关键字段：**
- `importance`：长期重要性（相对稳定）
- `progress ∈ [0,1]`：进展
- `activation ∈ [0,1]`：当前牵引强度（可随停滞上升）
- `next_step`：下一步可推进的小步（关键，避免空转）
- `status`：active / paused / done
- `source`：seed / need / affordance / memory / boredom / reflection
- `ttl_min`：生命周期上限（低活跃、低进展的会被暂停）

**动力学：**
- 未完成且停滞：activation 逐渐上升（stall_boost）
- 有进展：progress 上升，同时 activation 下降（“做了就不那么挂”）
- 完成：status=done 并归档

**竞价（bid）：**
- `(activation, importance, unfinished)` 组合计算，并可被地点线索 `context_cues` 增强。

---

### 2.3 Attention（注意竞争与工作记忆）
将 `Need` 与 `CurrentConcern` 统一放入候选集合，按 bid 排序，选 top-3：

输出到 state：
- `attention.top_foci`：top-3（含 share、bid、label）
- `attention.working_memory`：3条工作记忆字符串
- `attention.conflict`：一句冲突/取舍描述（用于叙事可视化）

> 目的：让“内在冲突与取舍”成为可见状态，而不是凭空写出来。

---

### 2.4 TODO（可选任务层）
TODO 不再只是文本列表，而是“可推进的对象”：
- `progress`、`next_step`、`blocked_reason`
- 模型可通过 `todo_ops` 更新/完成/新增

---

## 3. 世界模型（防止模型乱编环境）
v2 引入最小世界图：
- `WORLD_LOCATIONS`：允许地点集合
- `WORLD_NEIGHBORS`：相邻可达（防止“瞬移”）
- `posture`：lying/sitting/standing/walking

模型只能通过 `world_patch` 提议变更，代码会校验：
- 位置必须是**相邻**或不变
- 姿势必须在允许集合

---

## 4. 关切生成与裁撤（生命周期管理）

### 4.1 为什么需要“生成/裁撤”
长期运行中，关切集合必须变化，否则：
- 要么永远重复少数固定项目
- 要么模型“叙事换话题”但系统状态不变，仍会卡死

### 4.2 自动生成（spawn）来源（数据驱动）
v2 采用**规则表 CONCERN_RULES** + 轻量主题抽取：
- **need-based**：当任一 need 高位时，生成“先处理：X”的关切（模板化，不为每个 need 写死逻辑）
- **affordance-based**：当前环境 affordances 提供机会 → 生成顺手小项目
- **boredom-based**：无聊/循环时生成“打破循环”的关切
- **memory-topic-based**：从 recent/episodic 抽取高频主题词，生成“别让某主题一直搅”的关切

生成时有：
- `min_novelty`（与已有 active concern 的字符 Jaccard 相似度去重）
- 每次最多新增 1 条（由 cooldown 控制）

### 4.3 裁撤/限额（prune/limit）
- done → archive
- TTL 超时且低 activation/低 progress → paused
- active 数量超过 `max_active` → 按 bid 低者降级为 paused

### 4.4 reflection_mode（低频反思窗口）
每隔 `reflection_every_ticks`（默认 20min）或卡死/高无聊触发：
- 允许模型输出 `concern_ops`：
  - add（最多1）
  - pause/retire（各最多几条）
- 仍受 novelty/cooldown/max_active 限制，避免刷屏。

> 目的：让主体能“自我重组长期项目”，但保持工程可控。

---

## 5. 反卡死机制（Loop Guard）

state 维护：
- `last_tags`（最近10步）
- `repeat_last_tag_count`（重复程度）
- `no_progress_ticks`（没有 concern/todo 进展的连续tick数）
- `boredom`（无聊/习惯化）

触发条件（任一满足）：
- `repeat_last_tag_count >= 3`
- `no_progress_ticks >= 25`
- `boredom >= 0.80`

触发后**硬约束**：模型的 `action.tag` 必须是
- `meta_replan` 或 `context_change` 或 `recovery`

并且代码层面会因重复 tag 自动提升 boredom，从而形成负反馈闭环。

---

## 6. 记忆系统（长期运行不会爆）

### 6.1 recent（短期行为轨迹）
- 保存最近 `RECENT_KEEP` 条（默认120）

### 6.2 episodic（情景记忆 gist）
- state 内只保留 `EPISODIC_KEEP` 条（默认600）
- 超出的部分写入 `episodic_archive.jsonl` 追加归档（可长期运行）

### 6.3 semantic（语义/信念更新）
- 由模型通过 `memory_ops.semantic_update` 更新
- state 内限制 `SEMANTIC_KEEP`

### 6.4 防记忆污染
prompt 明确提供 `memory_ops.suppress_as_nonfact`：  
要求模型将“推测/反刍/想象”标记为非事实，不当作发生事件写入。

---

## 7. 模型输出（每 tick 的 JSON 合同）

### 7.1 核心原则
- **动作仍然只有一个**，<=30秒（保证真实时间尺度）
- 但允许同 tick 输出丰富的“内部更新”：plan、memory_ops、理解/不确定性等

### 7.2 必须字段
- `action`
- `memory`
- `meta`

### 7.3 可选字段（强烈建议输出）
- `effects`：need_delta / concern_progress / todo_progress / boredom_delta
- `plan`：未来3~6步草案（不等于执行）
- `memory_ops`：写 episodic/semantic 的结构化决定
- `todo_ops`：增改完
- `concern_ops`：仅 reflection_mode 时允许
- `world_patch`：地点/姿势（代码校验）

---

## 8. 确定性“物理效果”（让系统不依赖模型自觉）
模型可能忘记写 effects，因此 v2 引入 `TAG_NEED_HEURISTICS`：
- 比如 tag=drink 会自动降低 thirst
- tag=toilet 会降低 bladder
- tag=ruminate 会上升 stress
- tag=meta_replan 会略降 stress

这保证即使模型输出质量波动，系统仍有可见动态。

---

## 9. 运行流程（每30秒）

1) 对齐墙钟（可关闭）  
2) `tick_internal_dynamics()`：
   - needs 上升
   - concerns 激活更新（停滞→更牵引）
   - 定期 spawn/prune concerns
   - 计算 attention top-3
3) 构建 prompt（带世界、needs、concerns、top_foci、guard 状态等）
4) 调 LLM 输出 JSON
5) `apply_update()`：
   - 校验 action / world_patch
   - 应用 effects + tag heuristics
   - 更新 loop_guard（boredom / no_progress / repeat）
   - 写 recent/episodic（并归档溢出）
   - 执行 todo_ops / concern_ops / memory_ops / plan_queue
6) 保存 `state.json`

若 LLM 失败/非 JSON：
- 写入 recent 错误
- 用 `fallback_action()` 保持系统自稳连续性

---

## 10. 文件与持久化约定

- `./life_state/state.json`：唯一主状态（可随时停机/恢复）
- `./life_state/episodic_archive.jsonl`：情景记忆溢出归档（无限增长但可按行处理）

> 设计目标是：**状态可恢复、可长期跑、可用于后期视频/文章素材提取**。

---

## 11. 安全与运行注意
- 代码中禁用“外部工具调用”的行为，仅作为叙事主体控制器

---

## 12. 可扩展点（留给以后更复杂的心理孪生/认知架构）
- 更细的 need（疼痛、冷热、药物、社交拒绝敏感等）
- 更强的世界模型（对象/房间物品、日程事件）
- 更正式的记忆检索（向量检索、情景-语义联结）
- 引入执行功能模块（抑制、切换成本、任务集维持）
- 多速率调度（30秒动力学，但每2-5分钟才问一次 LLM，中间执行 plan_queue）

---

如果你希望“下一位 AI 不乱”，我建议你把这份文档与代码一起放仓库，并在 state.json 里加一个 `doc_version: "v2"` 字段，后续升级时保持向后兼容（dataclass 字段加默认值、加载时过滤未知字段）。
