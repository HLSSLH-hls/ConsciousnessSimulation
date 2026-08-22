

# 关切驱动的数字认知主体（Needs–Concerns–Goals–Attention–Memory）

## 注意
目前用的是Deepseek的API，这个请使用者将自己的API_KEY放在config.py的对应字符串常量里面即可运行。
## 0. 项目目标（Project Goal）
本项目实现一个以 **30 秒为时间步**运行的“数字认知主体控制器”。系统以可持续运行的方式整合：

- 内稳态驱动（Needs）
- 深层价值与未竟挂念（Deep Concern + Current Concern）
- 可验证推进的短期目标（Goals with evidence）
- 注意竞争与打断（Attention competition with interrupt）
- 事件化的情节记忆与巩固（Event-based episodic memory + consolidation）
- 环境观察与可行动性线索（World observation + affordances）

系统不是在宣称“主观意识已被证明”，而是实现一种 **可运行的动机-注意-记忆闭环**，并为进一步引入现象学/自我模型提供工程基础。

---

## 1. 理论预设（Theoretical Assumptions）
本系统的设计借鉴以下认知科学/心理学思想（工程化实现，非严格神经机制复刻）：

1) **内稳态驱动（Homeostasis / Drives）**  
需求随时间上升；满足/缓解后下降。需求在注意竞争中具有优先级，并可在阈值时打断其他活动。

2) **深层关切 vs 当前关切（Deep Concerns vs Current Concerns, Klinger 风格）**  
- **DeepConcern**：稳定价值/长期在乎什么（“我这个人在乎什么”）。  
- **CurrentConcern**：未竟承诺/张力（tension）构成的介观动力单元，具有压抑/回弹倾向，可持续偏置注意、目标与记忆。

3) **有限理性与偏置竞争（Biased Competition / Global Workspace 风格的工程对应）**  
注意系统在多个候选之间竞争，输出少量“焦点”供决策使用；并存在 bottom-up 打断（need/world）与 top-down 维持（concern/goal）的融合。

4) **记忆的事件分段（Event Segmentation）与结果门控（Outcome/Predictive Error proxy）**  
情节记忆以事件为单位组织，不以 tick 流水账形式写入；边界触发后，系统进行压缩与巩固（可通过 LLM 进行结构化摘要与抽取）。

5) **可验证推进（Evidence-based Progress）**  
目标进度不是语言自报，而需绑定到行动后果、need 变化、位置变化、patch 成功等证据；系统保存证据日志以对抗“幻觉式完成”。

---

## 2. 总体架构概览（High-level Architecture）

### 2.1 主循环相位（每 30 秒 tick）
```
(1) 内部动态：needs↑, working memory衰减, current concerns动态
(2) 世界观察：WorldAgent周期性生成 world_obs（可复用上一帧）
(3) 目标生成：从 current concerns + 情境 + 记忆摘要 生成/更新 goals（周期性）
(4) 注意竞争：need / current concern / goals / world-salience 竞争 -> top_foci + 推荐tag
(5) 记忆检索：按 top_foci + location 取回事件/语义/策略 -> 推入工作记忆
(6) 决策：LLM 输出下一步微行动 JSON（含 need_updates、goal_ops、concern_ops 等）
(7) 执行应用：world_patch验证；need_updates校验限幅；goal_ops证据验证；更新loop_guard
(8) 事件化记忆：写入TickRecord到事件缓冲；若边界触发，阻塞调用LLM压缩成事件记忆
(9) 巩固：周期性抽取 semantic/option，遗忘/压缩低价值事件
(10) 持久化：core_state + memory + concerns + goals + world_observation
```

### 2.2 模块边界（Modules）
- `world_agent.py`：环境观察器（LLM产生结构化 world_obs；动作patch验证 location/posture）
- `concern_system.py`：DeepConcern + CurrentConcern（tension/suppression/rebound）
- `goal_system.py`：目标与证据日志；进度必须通过 ExecutionEvidence 验证
- `attention.py`：注意竞争（含阈值 interrupt、情绪/唤醒调制代理、合理重复不惩罚）
- `memory_system.py`：事件化情节记忆 + 语义记忆 + 策略（option）+ 工作记忆；LLM压缩与巩固
- `main.py`：控制器，定义 tick 相位与数据流

---

## 3. 核心数据结构（Core State & Contracts）

### 3.1 Needs（内稳态）
每个 need 具有：
- `level`（0..1），随时间上升
- `apply_delta(delta, satisfied)`：支持“缓解”与“满足后额外回落”
- 注意：系统采用 **LLM 直接输出 need_updates**，但代码层会根据动作类型、affordances、patch_ok、时长等进行 **可行性校验与限幅**。

LLM 决策输出片段（简化）：
```json
"effects": {
  "need_updates": [
    {"need_id":"need_thirst","delta":-0.06,"satisfied":false,"reason":"..."}
  ]
}
```

### 3.2 DeepConcern vs CurrentConcern
- DeepConcern：稳定价值（importance/domain/context_triggers）
- CurrentConcern：介观动力学（tension/suppression/rebound/urgency + cues + linked_goal_ids）

CurrentConcern 是注意竞争与目标生成的主要动机输入。

### 3.3 Goal with Evidence
Goal 结构含：
- `progress`（0..1）
- `evidence_requirements`：例如 tags_any、location_any、need_relief_min、require_patch_ok
- `evidence_log`：每次 progress 提议的 accept/cap/reject 记录

Goal 的进度更新流程：
- LLM 可以提议 `goal_ops.progress`
- 系统用 `ExecutionEvidence`（动作/need差分/位置变化/patch_ok）进行验证并限幅

### 3.4 AttentionResult
注意输出包含：
- `top_foci[]`：候选焦点（layer/id/score/share/tag_hint）
- `interrupt`：是否触发 need 阈值打断
- `recommended_tag`：建议行为类别（不强制）

### 3.5 Memory（事件化）
- TickRecord：每 tick 的客观记录（动作、need前后、世界事件、focus ids、goal/cc ids）
- EpisodicEvent：多个 TickRecord 压缩为一个事件记忆（gist/detail/salience/affect + 索引）
- SemanticItem：语义命题（带证据 event_id）
- OptionItem：策略片段（cue→action→outcome）

**关键点**：情节记忆编码是“事件边界触发时”发生，而不是每 tick 写一条。

---

## 4. 世界模块（WorldAgent）与权责说明
当前实现中：
- `WorldAgent.observe()`：LLM 生成世界观察快照（visual/auditory/somatic/affordances 等）
- `WorldAgent.apply_action_patch()`：验证 location/posture 相邻合法性并应用

> 说明：目前 world_obs 更像“观测/信念摘要”的混合体。未来计划将 world truth / observation / belief / affordances 更严格分层（见 Roadmap）。

---

## 5. 记忆系统实现要点（Memory System）
### 5.1 事件分段（Segmentation）
- 先规则检测边界（location变化、world_events、blocked、uncertainty、meta_tag、patch_fail、focus切换、need显著变化等）
- 边界触发后：阻塞调用 LLM 压缩为事件（gist/detail/tags/salience/affect + 少量 semantic 候选）

### 5.2 检索（Retrieval）
不使用 Jaccard。默认采用：
- n-gram hashing 向量相似度（近似 embedding）
- 结合 recency、affect congruence、goal/cc/location cue bonus、salience 与检索频率

并可并行检索：
- episodic events
- semantic
- options（策略）

### 5.3 巩固（Consolidation）
周期性选择高显著/高检索事件，用 LLM 抽取：
- semantic_claims（带 evidence_event_ids）
- options（可复用行动策略）
并对低价值旧事件做衰减/压缩，模拟遗忘。

---

## 6. 可控循环接口（run_controlled_loop）
系统支持前后端拆分：控制器可在后台循环，每 tick 通过回调输出轻量状态：
- tick_count/location/needs/recent_actions
建议未来扩展回调 payload：current_concerns、goals（含证据状态）、attention top_foci、boredom/no_progress 等。

---

## 7. 已有功能点清单（Current Feature Checklist）
- [x] 30 秒 tick 对齐 wall clock
- [x] Needs 上升与缓解（LLM need_updates + 校验限幅）
- [x] DeepConcern（稳定价值）+ CurrentConcern（tension/suppression/rebound）
- [x] Goals：由 current concerns 生成；进度由证据验证；保存 evidence_log
- [x] 注意竞争：interrupt、情绪/唤醒调制代理、合理重复不惩罚、world salience候选
- [x] 工作记忆衰减 + 检索推入WM
- [x] 事件化情节记忆：TickRecord→EventBuffer→LLM压缩为事件
- [x] 巩固：抽取 semantic 与 option；遗忘低价值事件
- [x] 持久化：core_state/memory/concerns/goals/world_observation

---

## 8. 局限与不足（Limitations）
1) **世界模型仍偏“叙事观测”**  
WorldAgent 目前可以生成丰富观察，但缺少严格的 world truth / belief 分离；affordances 也可能被 LLM 推断污染。

2) **缺少真正的“内源性思维流”**  
当前注意竞争主要在“已有候选”之间仲裁；人类式的 mind-wandering、intrusion、联想、后台想法并未作为独立模块实现。

3) **并行性不足**  
主循环是单线程/阻塞式；记忆 finalize 与 consolidate 会阻塞 tick（当前假设 30 秒足够容纳 LLM 往返，但高延迟时会失配）。

4) **情绪系统仍是代理变量**  
当前多用 stress/fatigue 作为 arousal proxy；尚未实现独立 EmotionSystem 的 appraisal/modulation/regulation。

5) **目标推进的证据验证仍是弱验证**  
目前以 tag/location/need relief 等软证据为主；缺少更细粒度的“世界状态变化证据”。

6) **缺少统一通信机制**  
模块之间主要靠 main 传参；随着模块增多，参数膨胀风险上升。

---

## 9. 未来路线（Roadmap）
### 9.1 自发想法（Endogenous Thought Generator）
新增 ThoughtSystem：
- 从 current concerns（suppression/rebound）、world cues、memory cueing、boredom 生成 thought candidates
- thought 作为候选层进入注意竞争（layer=thought）
- 引入 focus-lock（思维段）与产物驱动的完成判据（避免反刍卡死）

### 9.2 并行活动与通信管理（EventBus + Phase Commit）
引入事件总线与分相提交：
- 模块发布 `StateDelta/Outcome/Boundary` 小事件
- Global Workspace（容量受限广播包）只广播赢家内容
- heavy data（记忆库/世界图）按需查询
- tick 内按 phase（sense/compete/decide/act/learn）保证一致性与可调试性

### 9.3 世界模型分层（WorldState / Observation / Belief）
- WorldState：唯一写真值（模拟器）
- Observation：受注意门控的采样
- BeliefState：滤波/融合与不确定性管理
- Affordance/Option 由技能库/动作模型生成，而非世界叙事推断

### 9.4 情绪系统（EmotionSystem）
实现 appraisal（威胁/奖励/可控性/意外性）并调制：
- attention 的分散/聚焦
- memory 编码阈值与巩固优先级
- current concern intrusion/抑制能力
- goal 的坚持/暂停/放弃

### 9.5 现象学中介层（Experience Field M）
若后续要做意识体验结构建模：
- 将 attention 的 foreground/background/fringe 显式化为 M(t)
- 以 specious present 窗口组织显现内容
- 用作：记忆编码门控、叙事自我、前端可视化与“意识转移”演示脚本生成

---

## 10. 状态文件与目录结构（State Layout）
默认状态目录：`./agent_state/`

- `core_state.json`：tick_count、needs、loop_guard、last_action、recent_log
- `world_observation.json`：最新 world_obs
- `concerns.json`：`{deep_concerns:[], current_concerns:[]}`
- `goals.json`：goals（含 evidence_log）
- `memory/`：
  - `episodic_events_hot.json`
  - `episodic_events_archive.jsonl`
  - `semantic.json`
  - `options.json`
  - `working.json`
  - `event_buffer.json`

---
