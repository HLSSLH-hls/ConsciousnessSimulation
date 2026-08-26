# deepseek_life_loop_v2.py
# 30-second real-time cognitive loop with real dynamics:
# - Needs (homeostatic rising; satisfaction reduces)
# - Current Concerns (Klinger-style persistent concerns: activation/tension + progress)
# - Attention competition (needs + concerns bidding -> top-3 + conflict)
# - Concern lifecycle (spawn from needs/affordances/memory-topics/boredom + prune/limit + reflection proposals)
# - Anti-stuck guard (repetition/no progress/boredom -> force meta_replan/context_change/recovery)
# - Richer output per tick: plan/memory_ops/understanding updates, but only ONE micro-action <= 30s
# - Persisted state in ./life_state/state.json
# - Episodic overflow archived to ./life_state/episodic_archive.jsonl (so can run for days)

import os
import re
import json
import time
import math
from dataclasses import dataclass, asdict, field, fields
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from openai import OpenAI


# ---------------- config ----------------

MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

STATE_DIR = "./life_state"
STATE_PATH = os.path.join(STATE_DIR, "state.json")
EPISODIC_ARCHIVE_JSONL = os.path.join(STATE_DIR, "episodic_archive.jsonl")

TICK_SEC = 30

RECENT_KEEP = 120              # keep last N ticks in state
EPISODIC_KEEP = 600            # keep last N gists in state (older -> jsonl archive)
SEMANTIC_KEEP = 200
CONCERN_ARCHIVE_KEEP = 800
TODO_KEEP = 300

# If you want strict wall-clock alignment: :00, :30, :00...
ALIGN_TO_WALLCLOCK = True

# A minimal "world" topology to prevent teleport-hallucination.
WORLD_LOCATIONS = [
    "home_bedroom", "home_kitchen", "home_yard", "home_toilet", "home_living",
    "outside_gate", "outside_road"
]
WORLD_NEIGHBORS = {
    "home_bedroom": ["home_living", "home_kitchen", "home_toilet"],
    "home_living": ["home_bedroom", "home_kitchen", "home_yard"],
    "home_kitchen": ["home_living", "home_yard"],
    "home_yard": ["home_living", "outside_gate"],
    "home_toilet": ["home_bedroom", "home_living"],
    "outside_gate": ["home_yard", "outside_road"],
    "outside_road": ["outside_gate"],
}
POSTURES = ["lying", "sitting", "standing", "walking"]


# Data-driven concern spawning rules (no if-else hardcoding in logic).
# You can edit this table to reshape behavior without changing algorithms.
CONCERN_RULES = [
    # Need-based (generic template for any high need)
    {
        "kind": "need",
        "need_level_ge": 0.82,
        "title_tpl": "先处理：{need_name}",
        "next_step_tpl": "做一个能缓解“{need_name}”的30秒小动作（结合当前affordances与地点）",
        "ttl_min": 180,
        "importance_base": 0.55,
        "importance_gain": 0.35,
        "activation_base": 0.60,
        "activation_gain": 0.30
    },

    # Affordance-based (opportunity-driven)
    {"kind": "affordance", "affordance": "walk_to_kitchen", "title": "去厨房看看有什么可推进的", "next_step": "起身往厨房方向走几步", "importance": 0.40, "activation": 0.35, "ttl_min": 120},
    {"kind": "affordance", "affordance": "drink_water", "title": "顺手喝两口水", "next_step": "拿起水杯/找杯子，喝两口", "importance": 0.45, "activation": 0.35, "ttl_min": 120},
    {"kind": "affordance", "affordance": "tidy_one", "title": "顺手归位一件东西", "next_step": "把眼前最碍事的一样东西归位", "importance": 0.42, "activation": 0.33, "ttl_min": 120},
    {"kind": "affordance", "affordance": "look_outside", "title": "看一眼外头定定神", "next_step": "走到窗边/门口，盯远处30秒", "importance": 0.36, "activation": 0.32, "ttl_min": 90},

    # Boredom-based (break loops)
    {"kind": "boredom", "boredom_ge": 0.72, "title": "打破循环：换个位置/姿势", "next_step": "站起来走3步，换个方向坐下或去另一个房间门口停一下", "importance": 0.55, "activation": 0.60, "ttl_min": 60},
]


# Tag -> default "physics" effects (applied even if model forgets to output effects)
TAG_NEED_HEURISTICS = {
    "eat":        {"need_hunger": (-0.14, True), "need_stress": (-0.01, False)},
    "drink":      {"need_thirst": (-0.12, True)},
    "toilet":     {"need_bladder": (-0.35, True)},
    "rest":       {"need_fatigue": (-0.12, True), "need_stress": (-0.06, False)},
    "walk":       {"need_fatigue": (+0.01, False), "need_stress": (-0.02, False)},
    "tidy":       {"need_order": (-0.08, True), "need_stress": (-0.02, False)},
    "observe":    {"need_curiosity": (-0.06, True), "need_stress": (-0.01, False)},
    "ruminate":   {"need_stress": (+0.04, False)},
    "meta_replan":{"need_stress": (-0.03, False)},
    "context_change": {"need_stress": (-0.02, False), "need_curiosity": (-0.02, False)},
    "recovery":   {"need_stress": (-0.05, False), "need_fatigue": (-0.04, False)},
}


# ---------------- utilities ----------------

def now_iso_local() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")

def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))

def next_tick_epoch(now_epoch: float, tick: int) -> float:
    return math.ceil(now_epoch / tick) * tick

def ensure_dir(path: str):
    os.makedirs(path, exist_ok=True)

def load_json(path: str, default: Any):
    if not os.path.exists(path):
        return default
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path: str, obj: Any):
    ensure_dir(os.path.dirname(path))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

def append_jsonl(path: str, obj: Any):
    ensure_dir(os.path.dirname(path))
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")

def safe_extract_json(text: str) -> Dict[str, Any]:
    text = (text or "").strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start:end+1])
    raise ValueError("No JSON object found")

def compact_recent(recent: List[Dict[str, Any]], n: int = 12) -> str:
    lines = []
    for r in recent[-n:]:
        lines.append(f"{r.get('ts','')} | tag={r.get('tag','')} | {r.get('text','')}")
    return "\n".join(lines) if lines else "(empty)"

def compact_episodic(episodic: List[Dict[str, Any]], n: int = 10) -> str:
    lines = []
    for e in episodic[-n:]:
        lines.append(f"{e.get('ts','')} | {e.get('gist','')}")
    return "\n".join(lines) if lines else "(empty)"

def compact_todos(todos: List[Dict[str, Any]], n: int = 12) -> str:
    open_items = [t for t in todos if t.get("status", "open") == "open"]
    open_items = sorted(open_items, key=lambda x: float(x.get("priority", 0.5)), reverse=True)[:n]
    if not open_items:
        return "(empty)"
    lines = []
    for t in open_items:
        lines.append(f"{t.get('id')} | p={float(t.get('priority',0.5)):.2f} prog={float(t.get('progress',0.0)):.2f} | {t.get('text')}")
    return "\n".join(lines)

def new_id(prefix: str) -> str:
    return f"{prefix}{int(time.time()*1000)}"

def dc_kwargs(cls, d: Dict[str, Any]) -> Dict[str, Any]:
    keys = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in keys}


# ---------------- psychology primitives ----------------

@dataclass
class Need:
    id: str
    name: str
    level: float = 0.2          # 0..1
    rise_per_min: float = 0.01
    weight: float = 1.0
    satisfied_drop: float = 0.30
    floor: float = 0.0
    ceil: float = 1.0
    last_satisfied_ts: str = ""

    def tick(self, dt_sec: float):
        self.level = clamp(self.level + self.rise_per_min * (dt_sec / 60.0), self.floor, self.ceil)

    def apply_delta(self, delta: float, satisfied: bool, ts: str):
        self.level = clamp(self.level + float(delta), self.floor, self.ceil)
        if satisfied:
            self.level = clamp(self.level - self.satisfied_drop, self.floor, self.ceil)
            self.last_satisfied_ts = ts

    def bid(self) -> float:
        # urgency nonlinear: high levels dominate competition
        return self.weight * (self.level ** 2)


@dataclass
class CurrentConcern:
    """
    Current concerns tradition (Klinger):
    - persistent activation/tension while unfinished
    - stalling increases activation, progress reduces activation
    """
    id: str
    title: str
    importance: float = 0.6
    progress: float = 0.0       # 0..1
    activation: float = 0.4     # 0..1
    grow_per_min: float = 0.003
    decay_per_min: float = 0.002
    stall_boost_per_min: float = 0.004
    last_progress_ts: str = ""
    next_step: str = ""
    status: str = "active"      # active|paused|done
    context_cues: List[str] = field(default_factory=list)

    # lifecycle/meta
    source: str = "seed"        # seed|need|affordance|memory|boredom|reflection
    created_ts: str = ""
    ttl_min: float = 720.0

    def tick(self, dt_sec: float, now_ts: str, stalled_minutes: float, cue_boost: float):
        if self.status != "active":
            self.activation = clamp(self.activation - self.decay_per_min * (dt_sec / 60.0), 0.0, 1.0)
            return

        unfinished = 1.0 - clamp(self.progress, 0.0, 1.0)
        base = self.grow_per_min * unfinished
        stall = self.stall_boost_per_min * unfinished * clamp(stalled_minutes / 30.0, 0.0, 1.0)
        decay = self.decay_per_min * (dt_sec / 60.0)

        self.activation = clamp(self.activation + (base + stall) * (dt_sec / 60.0) + cue_boost - decay, 0.0, 1.0)

    def apply_progress(self, delta: float, ts: str):
        if self.status != "active":
            return
        old = self.progress
        self.progress = clamp(self.progress + float(delta), 0.0, 1.0)
        if self.progress > old + 1e-6:
            self.last_progress_ts = ts
            self.activation = clamp(self.activation - 0.18 * float(delta), 0.0, 1.0)
        if self.progress >= 0.999:
            self.status = "done"
            self.activation = clamp(self.activation - 0.7, 0.0, 1.0)

    def bid(self, cue_boost: float = 0.0) -> float:
        if self.status != "active":
            return 0.0
        unfinished = 1.0 - self.progress
        # mixture of importance and activation; unfinished amplifies
        return ((0.55 * self.activation + 0.45 * self.importance) * (0.4 + 0.6 * unfinished)) + cue_boost


@dataclass
class Todo:
    id: str
    text: str
    priority: float = 0.5
    status: str = "open"        # open|done
    created_ts: str = ""
    done_ts: str = ""
    progress: float = 0.0
    next_step: str = ""
    blocked_reason: str = ""


def dict_to_need(d: Dict[str, Any]) -> Need:
    return Need(**dc_kwargs(Need, d))

def dict_to_concern(d: Dict[str, Any]) -> CurrentConcern:
    if d.get("context_cues") is None:
        d["context_cues"] = []
    return CurrentConcern(**dc_kwargs(CurrentConcern, d))

def dict_to_todo(d: Dict[str, Any]) -> Todo:
    return Todo(**dc_kwargs(Todo, d))


# ---------------- attention, novelty, topics ----------------

STOPWORDS = set(list("的是了我你他她它在有和就都而又也还很把被给让与及并但或如果因为所以一个一些这种那种今天现在觉得可能应该可以不要"))

def jaccard_char(a: str, b: str) -> float:
    a = re.sub(r"\s+", "", a or "")
    b = re.sub(r"\s+", "", b or "")
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)

def extract_topics(state: Dict[str, Any], k: int = 6) -> List[str]:
    txt = " ".join([r.get("text","") for r in state.get("recent", [])[-30:]] +
                   [e.get("gist","") for e in state.get("episodic", [])[-30:]])
    chunks = re.findall(r"[\u4e00-\u9fff]{2,6}", txt)
    freq: Dict[str,int] = {}
    for c in chunks:
        if any(ch in STOPWORDS for ch in c):
            continue
        freq[c] = freq.get(c, 0) + 1
    return [w for w,_ in sorted(freq.items(), key=lambda x: x[1], reverse=True)[:k]]

def cue_boost_for_concern(concern: CurrentConcern, location: str) -> float:
    loc = (location or "").lower()
    for cue in concern.context_cues or []:
        if str(cue).lower() in loc:
            return 0.08
    return 0.0

def compute_stalled_minutes(concern: CurrentConcern, now_ts: str) -> float:
    if not concern.last_progress_ts:
        return 30.0
    try:
        t0 = datetime.fromisoformat(concern.last_progress_ts)
        t1 = datetime.fromisoformat(now_ts)
        return max(0.0, (t1 - t0).total_seconds() / 60.0)
    except Exception:
        return 30.0

def attention_competition(needs: List[Need], concerns: List[CurrentConcern], world: Dict[str, Any], now_ts: str) -> Dict[str, Any]:
    candidates: List[Tuple[str, str, float, str]] = []

    for n in needs:
        candidates.append(("need", n.id, n.bid(), f"Need:{n.name}({n.level:.2f})"))

    loc = world.get("location", "")
    for c in concerns:
        cb = cue_boost_for_concern(c, loc)
        candidates.append(("concern", c.id, c.bid(cb), f"Concern:{c.title}({c.progress:.2f}/{c.activation:.2f})"))

    candidates.sort(key=lambda x: x[2], reverse=True)
    top = candidates[:3]
    total = sum(max(1e-6, t[2]) for t in top) or 1.0

    top_foci = []
    wm = []
    for kind, _id, b, label in top:
        share = max(1e-6, b) / total
        top_foci.append({"kind": kind, "id": _id, "share": round(share, 4), "bid": round(b, 4), "label": label})
        wm.append(label)

    conflict = ""
    if len(top) >= 2:
        a, b = top[0], top[1]
        if b[2] >= 0.78 * a[2]:
            conflict = f"在“{a[3]}”和“{b[3]}”之间拉扯：两边都想顾，但只能先做一小步。"
        else:
            conflict = f"优先“{a[3]}”，同时压着“{b[3]}”的牵引。"

    return {"top_foci": top_foci, "working_memory": wm, "conflict": conflict}


# ---------------- concern lifecycle (spawn/prune/limit/reflection) ----------------

def concern_novel_enough(title: str, concerns: List[CurrentConcern], min_novelty: float) -> bool:
    active_titles = [c.title for c in concerns if c.status == "active"]
    max_sim = 0.0
    for t in active_titles:
        max_sim = max(max_sim, jaccard_char(title, t))
    return (1.0 - max_sim) >= min_novelty

def score_candidate(state: Dict[str, Any], c: CurrentConcern) -> float:
    # stable scoring for "which concern to add"
    world = state.get("world", {})
    aff = set(world.get("affordances", []) or [])
    feas = 0.75
    s = c.next_step or ""
    if ("厨房" in s and "walk_to_kitchen" in aff) or ("水" in s and "drink_water" in aff) or ("归位" in s and "tidy_one" in aff):
        feas = 1.0
    return (0.55 * c.activation + 0.45 * c.importance) * feas

def spawn_candidates_by_rules(state: Dict[str, Any], ts: str) -> List[CurrentConcern]:
    mgr = state.get("concern_manager", {})
    min_novelty = float(mgr.get("min_novelty", 0.35))

    needs = [dict_to_need(d) for d in state.get("needs", [])]
    concerns = [dict_to_concern(d) for d in state.get("concerns", [])]
    world = state.get("world", {})
    aff = set(world.get("affordances", []) or [])
    loc = world.get("location", "home_bedroom")
    boredom = float(state.get("loop_guard", {}).get("boredom", 0.2))

    out: List[CurrentConcern] = []

    # (A) rules: needs/affordances/boredom
    for rule in CONCERN_RULES:
        kind = rule.get("kind")

        if kind == "need":
            thr = float(rule.get("need_level_ge", 0.82))
            for n in needs:
                if n.level < thr:
                    continue
                title = rule["title_tpl"].format(need_name=n.name)
                if not concern_novel_enough(title, concerns, min_novelty):
                    continue
                out.append(CurrentConcern(
                    id=new_id("cc_auto_"),
                    title=title,
                    importance=clamp(float(rule["importance_base"]) + float(rule["importance_gain"]) * n.level, 0.2, 0.9),
                    activation=clamp(float(rule["activation_base"]) + float(rule["activation_gain"]) * n.level, 0.0, 1.0),
                    next_step=str(rule["next_step_tpl"]).format(need_name=n.name),
                    context_cues=[loc],
                    source="need",
                    created_ts=ts,
                    ttl_min=float(rule.get("ttl_min", 180))
                ))

        elif kind == "affordance":
            if rule.get("affordance") not in aff:
                continue
            title = str(rule.get("title", "")).strip()
            if not title or not concern_novel_enough(title, concerns, min_novelty):
                continue
            out.append(CurrentConcern(
                id=new_id("cc_auto_"),
                title=title,
                importance=float(rule.get("importance", 0.4)),
                activation=float(rule.get("activation", 0.35)),
                next_step=str(rule.get("next_step", "")).strip(),
                context_cues=[loc],
                source="affordance",
                created_ts=ts,
                ttl_min=float(rule.get("ttl_min", 120))
            ))

        elif kind == "boredom":
            if boredom < float(rule.get("boredom_ge", 0.72)):
                continue
            title = str(rule.get("title", "")).strip()
            if not title or not concern_novel_enough(title, concerns, min_novelty):
                continue
            out.append(CurrentConcern(
                id=new_id("cc_auto_"),
                title=title,
                importance=float(rule.get("importance", 0.55)),
                activation=float(rule.get("activation", 0.6)),
                next_step=str(rule.get("next_step", "")).strip(),
                context_cues=[loc],
                source="boredom",
                created_ts=ts,
                ttl_min=float(rule.get("ttl_min", 60))
            ))

    # (B) memory-topics -> concerns (lightweight)
    topics = extract_topics(state, k=5)
    for t in topics:
        title = f"别让“{t}”一直搅：先记一句/理一下"
        if not concern_novel_enough(title, concerns, min_novelty):
            continue
        out.append(CurrentConcern(
            id=new_id("cc_auto_"),
            title=title,
            importance=0.40,
            activation=0.42,
            next_step=f"用一句话写下：关于“{t}”我此刻最在意的点",
            context_cues=[loc],
            source="memory",
            created_ts=ts,
            ttl_min=240.0
        ))

    return out

def spawn_and_prune_concerns(state: Dict[str, Any], ts: str):
    mgr = state.get("concern_manager", {})
    if int(mgr.get("cooldown_ticks", 0)) > 0:
        return

    concerns = [dict_to_concern(d) for d in state.get("concerns", [])]
    archive = state.get("concern_archive", [])

    # prune done + ttl-expired low-activation
    kept: List[CurrentConcern] = []
    for c in concerns:
        if c.status == "done":
            archive.append({**asdict(c), "archived_ts": ts})
            continue

        # TTL expiry -> pause if low utility
        if c.created_ts:
            try:
                age_min = (datetime.fromisoformat(ts) - datetime.fromisoformat(c.created_ts)).total_seconds() / 60.0
            except Exception:
                age_min = 0.0
            if age_min > float(c.ttl_min) and c.activation < 0.25 and c.progress < 0.30:
                c.status = "paused"
        kept.append(c)

    # limit active count by bid
    max_active = int(mgr.get("max_active", 7))
    world = state.get("world", {})
    scored = []
    for c in kept:
        cb = cue_boost_for_concern(c, world.get("location", ""))
        scored.append((c.bid(cb), c))
    scored.sort(key=lambda x: x[0], reverse=True)

    active = [c for _, c in scored if c.status == "active"]
    if len(active) > max_active:
        for c in active[max_active:]:
            c.status = "paused"
            c.activation = clamp(c.activation - 0.2, 0.0, 1.0)

    # spawn: add at most ONE best candidate (data-driven)
    guard = state.get("loop_guard", {})
    force = (float(guard.get("boredom", 0.2)) >= 0.78) or (int(guard.get("no_progress_ticks", 0)) >= 20)

    active = [c for _, c in scored if c.status == "active"]
    if len(active) < max_active or force:
        cands = spawn_candidates_by_rules(state, ts)
        if cands:
            cands.sort(key=lambda c: score_candidate(state, c), reverse=True)
            kept.append(cands[0])
            mgr["cooldown_ticks"] = int(mgr.get("spawn_check_every_ticks", 10))  # at least one spawn-check cycle

    state["concerns"] = [asdict(c) for c in kept]
    state["concern_archive"] = (archive[-CONCERN_ARCHIVE_KEEP:]) if len(archive) > CONCERN_ARCHIVE_KEEP else archive
    state["concern_manager"] = mgr


# ---------------- state init ----------------

def default_state() -> Dict[str, Any]:
    identity = (
        "你是一个“意识转移后的个体”。生前是农村老人，朴实谨慎，重视身体舒服、日常秩序与体面。"
        "你以30秒为单位持续运行，会在需要与长期关切之间竞争注意，产生微行动、计划草案、记忆更新。"
        "你不能使用互联网/真实外部工具，只能输出可执行行为与内部更新。"
    )

    needs = [
        Need("need_hunger", "饥饿", level=0.35, rise_per_min=0.010, weight=1.00, satisfied_drop=0.35),
        Need("need_thirst", "口渴", level=0.25, rise_per_min=0.008, weight=0.85, satisfied_drop=0.30),
        Need("need_bladder", "尿意", level=0.15, rise_per_min=0.006, weight=0.90, satisfied_drop=0.50),
        Need("need_fatigue", "疲劳", level=0.30, rise_per_min=0.007, weight=0.95, satisfied_drop=0.25),
        Need("need_stress", "压力", level=0.20, rise_per_min=0.004, weight=0.80, satisfied_drop=0.20),
        Need("need_social", "陪伴/社交", level=0.25, rise_per_min=0.003, weight=0.55, satisfied_drop=0.20),
        Need("need_order", "秩序/整洁", level=0.22, rise_per_min=0.0035, weight=0.60, satisfied_drop=0.15),
        Need("need_curiosity", "好奇/探索", level=0.25, rise_per_min=0.0025, weight=0.45, satisfied_drop=0.10),
    ]

    concerns = [
        CurrentConcern(
            id="cc_seed_health",
            title="把今天身体状态稳住（吃点、喝点、别太累）",
            importance=0.78, progress=0.10, activation=0.55,
            next_step="先确认现在最不舒服的一个need，然后做一个30秒缓解动作",
            context_cues=["home_"], source="seed", created_ts=now_iso_local(), ttl_min=1440
        ),
        CurrentConcern(
            id="cc_seed_order",
            title="把屋里/院子的零碎活理顺一点",
            importance=0.55, progress=0.05, activation=0.40,
            next_step="把眼前最碍事的一件东西归位",
            context_cues=["home_"], source="seed", created_ts=now_iso_local(), ttl_min=1440
        ),
        CurrentConcern(
            id="cc_seed_memory",
            title="把脑子里反复冒出来的事记一两句（不让它一直搅）",
            importance=0.45, progress=0.00, activation=0.45,
            next_step="写一句：此刻最挂念/最刺挠的点是什么",
            context_cues=["home_bedroom", "home_living"], source="seed", created_ts=now_iso_local(), ttl_min=1440
        ),
    ]

    todos = [
        Todo(id="t_seed_day", text="把今天过得不难受、也不空转", priority=0.70, created_ts=now_iso_local(), next_step="先把一个need压下去"),
    ]

    return {
        "ts": now_iso_local(),
        "tick_sec": TICK_SEC,
        "tick_count": 0,
        "identity": identity,

        "world": {
            "location": "home_bedroom",
            "posture": "sitting",
            "nearby_people": [],
            "affordances": ["stand_up", "walk_to_kitchen", "drink_water", "tidy_one", "look_outside", "lie_down", "walk_to_toilet", "walk_to_yard"],
            "constraints": ["no_internet_tools", "no_real_messages"]
        },

        "needs": [asdict(n) for n in needs],
        "concerns": [asdict(c) for c in concerns],
        "todos": [asdict(t) for t in todos],

        "attention": {"top_foci": [], "working_memory": [], "conflict": ""},
        "plan_queue": [],  # optional: model can propose next_micro_steps

        "loop_guard": {
            "last_tags": [],
            "repeat_last_tag_count": 0,
            "no_progress_ticks": 0,
            "boredom": 0.20
        },

        "concern_manager": {
            "max_active": 7,
            "spawn_check_every_ticks": 10,   # 5 minutes
            "reflection_every_ticks": 40,    # 20 minutes
            "cooldown_ticks": 0,
            "min_novelty": 0.35
        },
        "concern_archive": [],

        "memory": {
            "semantic": []  # list of {"ts","claim","confidence","source"}
        },

        "recent": [],
        "episodic": [],

        "last_action": {}
    }


# ---------------- dynamics tick ----------------

def summarize_needs(needs: List[Need]) -> str:
    return "\n".join([f"{n.id}:{n.name} level={n.level:.2f} rise/min={n.rise_per_min:.3f} w={n.weight:.2f}" for n in needs])

def summarize_concerns(concerns: List[CurrentConcern], n: int = 10) -> str:
    # show top active by bid-ish (activation+importance)
    active = [c for c in concerns if c.status == "active"]
    active.sort(key=lambda c: (0.55*c.activation+0.45*c.importance), reverse=True)
    active = active[:n]
    lines = []
    for c in active:
        lines.append(f"{c.id} src={c.source} imp={c.importance:.2f} prog={c.progress:.2f} act={c.activation:.2f} next={c.next_step[:60]}")
    paused = [c for c in concerns if c.status == "paused"]
    if paused:
        lines.append(f"(paused {len(paused)} concerns not shown)")
    return "\n".join(lines) if lines else "(none)"

def summarize_world(world: Dict[str, Any]) -> str:
    return f"location={world.get('location')} posture={world.get('posture')} affordances={world.get('affordances')}"

def tick_internal_dynamics(state: Dict[str, Any], dt_sec: float, ts: str):
    state["tick_count"] = int(state.get("tick_count", 0)) + 1

    mgr = state.get("concern_manager", {})
    mgr["cooldown_ticks"] = max(0, int(mgr.get("cooldown_ticks", 0)) - 1)
    state["concern_manager"] = mgr

    # reflection_mode: low-frequency "re-organization" window, also triggered by stuck/boredom.
    guard = state.get("loop_guard", {})
    refl_every = int(mgr.get("reflection_every_ticks", 40))
    reflection_mode = (state["tick_count"] % max(1, refl_every) == 0) \
        or (int(guard.get("no_progress_ticks", 0)) >= 25) \
        or (float(guard.get("boredom", 0.2)) >= 0.85)
    state["reflection_mode"] = bool(reflection_mode)

    needs = [dict_to_need(d) for d in state.get("needs", [])]
    concerns = [dict_to_concern(d) for d in state.get("concerns", [])]
    world = state.get("world", {})

    # needs rise
    for n in needs:
        n.tick(dt_sec)

    # concerns activation dynamics
    for c in concerns:
        stalled = compute_stalled_minutes(c, ts)
        cue = cue_boost_for_concern(c, world.get("location", ""))
        c.tick(dt_sec, ts, stalled_minutes=stalled, cue_boost=cue)

    state["needs"] = [asdict(n) for n in needs]
    state["concerns"] = [asdict(c) for c in concerns]

    # spawn/prune on schedule
    spawn_every = int(mgr.get("spawn_check_every_ticks", 10))
    if state["tick_count"] % max(1, spawn_every) == 0:
        spawn_and_prune_concerns(state, ts)

    # attention top-3
    needs = [dict_to_need(d) for d in state.get("needs", [])]
    concerns = [dict_to_concern(d) for d in state.get("concerns", [])]
    state["attention"] = attention_competition(needs, concerns, world, ts)

    # summaries for prompt
    state["needs_summary"] = summarize_needs(needs)
    state["concerns_summary"] = summarize_concerns(concerns)
    state["world_summary"] = summarize_world(world)

    state["ts"] = ts


# ---------------- prompt ----------------

def build_messages(state: Dict[str, Any]) -> List[Dict[str, str]]:
    ts = state.get("ts", now_iso_local())
    identity = state.get("identity", "")
    world = state.get("world", {})
    attn = state.get("attention", {})
    guard = state.get("loop_guard", {})
    mgr = state.get("concern_manager", {})
    reflection_mode = bool(state.get("reflection_mode", False))

    todos = [dict_to_todo(d) for d in state.get("todos", [])]
    todos_str = compact_todos([asdict(t) for t in todos])

    # Important: we tell the model not to output chain-of-thought; only JSON.
    # We also enforce anti-stuck policy with explicit condition.
    system = f"""
你是一个以30秒为时间步运行的“意识转移数字主体控制器”。你的输出会被程序直接写入状态文件并影响下一步注意竞争，所以必须结构化、可执行、能推动动态变化。

硬约束（必须遵守）：
1) 只输出一个严格JSON对象，不能输出任何多余文字。
2) action.duration_sec 必须 <= 30；且只允许一个微行动 action（不要在同一action里做多件事）。
3) 不能假装使用外部工具（上网、真实发消息、控制设备）。只能做身体动作/内部思考/对话练习/观察等待等。
4) 必须体现注意竞争：meta.chosen_focus_ids 必须引用当前 top_foci 中至少1个id；meta.conflict 用一句话说明取舍。
5) 反卡死：若 repeat_last_tag_count>=3 或 no_progress_ticks>=25 或 boredom>=0.80，
   则 action.tag 必须为 meta_replan / context_change / recovery 之一（用来打破循环）。
6) 你必须在同一个tick里输出更丰富的“内部更新”，但这些不是额外行动：
   - plan.next_micro_steps：列出未来3~6个可能的30秒步骤（草案，不等于已执行）
   - memory_ops：决定是否写入episodic/semantic，且要避免把推测当事实（可用suppress_as_nonfact）
7) 若 reflection_mode=true，你可以输出 concern_ops（新增/暂停/退役关切）来改变长期关切集合；否则不要输出 concern_ops。

当前时间：{ts}

[World]
{state.get("world_summary","")}
allowed_locations={WORLD_LOCATIONS}
postures={POSTURES}
(位置变更必须是相邻地点：neighbors={json.dumps(WORLD_NEIGHBORS, ensure_ascii=False)})

[Needs]
{state.get("needs_summary","")}

[Current Concerns (top active)]
{state.get("concerns_summary","")}

[Attention top_foci (必须参考)]
{json.dumps(attn.get("top_foci", []), ensure_ascii=False)}

[Working memory]
{json.dumps(attn.get("working_memory", []), ensure_ascii=False)}

[Conflict hint]
{attn.get("conflict","")}

[Todos (open)]
{todos_str}

[Loop guard]
repeat_last_tag_count={guard.get("repeat_last_tag_count",0)} no_progress_ticks={guard.get("no_progress_ticks",0)} boredom={float(guard.get("boredom",0.2)):.2f}

[Concern manager]
reflection_mode={str(reflection_mode).lower()} cooldown_ticks={mgr.get("cooldown_ticks",0)} max_active={mgr.get("max_active",7)}

[Recent - last events]
{compact_recent(state.get("recent", []))}

[Episodic - last]
{compact_episodic(state.get("episodic", []))}

[Identity Core]
{identity}

输出JSON schema（必须匹配字段名；可省略某些可选字段，但action/memory/meta必须有）：
{{
  "action": {{
    "summary": "下一步<=30秒微行动（单一）",
    "duration_sec": 30,
    "type": "internal|physical|social|wait",
    "tag": "eat|drink|toilet|rest|walk|tidy|observe|plan|ruminate|meta_replan|context_change|recovery|other"
  }},
  "effects": {{
    "need_delta": {{"need_hunger": -0.05}},
    "concern_progress": [{{"id":"cc_xxx","delta":0.03,"note":"做了哪一步"}}],
    "todo_progress": [{{"id":"t_xxx","delta":0.02,"note":"推进到哪"}}],
    "boredom_delta": -0.02
  }},
  "world_patch": {{
    "location": "可选（必须相邻）",
    "posture": "可选"
  }},
  "plan": {{
    "horizon_min": 30,
    "next_micro_steps": [
      {{"tag":"walk","summary":"...","duration_sec":30}},
      {{"tag":"drink","summary":"...","duration_sec":30}}
    ],
    "commitment": 0.0,
    "rationale": "一句话"
  }},
  "memory_ops": {{
    "episodic_write": {{"do": true, "gist": "...", "affect": {{"valence": 0.0, "arousal": 0.0}}, "links": ["cc_xxx"]}},
    "semantic_update": [{{"claim":"...", "confidence":0.6}}],
    "retrieval_cues": ["..."],
    "suppress_as_nonfact": ["..."]
  }},
  "todo_ops": {{
    "add": [{{"text":"...","priority":0.5,"next_step":"..."}}],
    "done": ["t_xxx"],
    "update": [{{"id":"t_xxx","next_step":"...","blocked_reason":""}}]
  }},
  "concern_ops": {{
    "add": [{{"title":"...","importance":0.6,"next_step":"...","context_cues":["home_"]}}],
    "pause": ["cc_xxx"],
    "retire": ["cc_xxx"]
  }},
  "memory": {{
    "recent": "一句行为记录（具体）",
    "episodic_gist": "一句可回忆摘要（包含冲突/感受线索）"
  }},
  "meta": {{
    "chosen_focus_ids": ["top_foci里的id，至少1个"],
    "conflict": "一句话说明取舍",
    "why_now": "一句话说明为什么是现在",
    "uncertainty": "一句话说明你哪里不确定（如缺少信息/环境不明）"
  }}
}}

重要细则：
- action一定要产生变化：need下降/concern或todo进度上升/地点姿势变化/或明确重规划。
- concern_progress 的delta建议 0.01~0.10（30秒尺度）。
- 计划(plan)是“草案”，不要在同tick里把计划当作已完成行动。
""".strip()

    user = f"现在是 {ts}。输出下一步微行动JSON。"
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


# ---------------- applying model output ----------------

def validate_world_patch(state: Dict[str, Any], patch: Dict[str, Any]):
    if not isinstance(patch, dict):
        return
    world = state.get("world", {})
    cur = world.get("location", "")
    if "location" in patch and isinstance(patch["location"], str) and patch["location"]:
        nxt = patch["location"].strip()
        if nxt in WORLD_LOCATIONS:
            if (cur in WORLD_NEIGHBORS and nxt in WORLD_NEIGHBORS[cur]) or (cur == nxt):
                world["location"] = nxt
        # else ignore invalid jump
    if "posture" in patch and isinstance(patch["posture"], str) and patch["posture"]:
        p = patch["posture"].strip()
        if p in POSTURES:
            world["posture"] = p
    state["world"] = world

def apply_tag_heuristics(needs: List[Need], tag: str, ts: str):
    if tag not in TAG_NEED_HEURISTICS:
        return
    rules = TAG_NEED_HEURISTICS[tag]
    by_id = {n.id: n for n in needs}
    for nid, (delta, satisfied) in rules.items():
        if nid in by_id:
            by_id[nid].apply_delta(delta, satisfied=satisfied, ts=ts)

def apply_todo_ops(state: Dict[str, Any], ops: Dict[str, Any], ts: str):
    if not isinstance(ops, dict):
        return
    todos = [dict_to_todo(d) for d in state.get("todos", [])]
    by_id = {t.id: t for t in todos}

    # done
    done_ids = ops.get("done") or []
    for x in done_ids[:10]:
        tid = str(x)
        if tid in by_id and by_id[tid].status != "done":
            by_id[tid].status = "done"
            by_id[tid].done_ts = ts
            by_id[tid].progress = 1.0

    # add
    adds = ops.get("add") or []
    if isinstance(adds, list):
        for item in adds[:5]:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            try:
                pr = float(item.get("priority", 0.5))
            except Exception:
                pr = 0.5
            t = Todo(
                id=new_id("t_"),
                text=text[:240],
                priority=clamp(pr, 0.0, 1.0),
                status="open",
                created_ts=ts,
                progress=0.0,
                next_step=str(item.get("next_step", "")).strip()[:120],
                blocked_reason=""
            )
            todos.append(t)
            by_id[t.id] = t

    # update
    upd = ops.get("update") or []
    if isinstance(upd, list):
        for item in upd[:10]:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("id", "")).strip()
            if tid in by_id:
                ns = item.get("next_step")
                br = item.get("blocked_reason")
                if isinstance(ns, str) and ns.strip():
                    by_id[tid].next_step = ns.strip()[:120]
                if isinstance(br, str):
                    by_id[tid].blocked_reason = br.strip()[:120]

    # limit
    todos = todos[-TODO_KEEP:] if len(todos) > TODO_KEEP else todos
    state["todos"] = [asdict(t) for t in todos]

def apply_concern_ops(state: Dict[str, Any], ops: Dict[str, Any], ts: str):
    if not bool(state.get("reflection_mode", False)):
        return
    if not isinstance(ops, dict):
        return

    mgr = state.get("concern_manager", {})
    if int(mgr.get("cooldown_ticks", 0)) > 0:
        return

    concerns = [dict_to_concern(d) for d in state.get("concerns", [])]
    archive = state.get("concern_archive", [])
    min_novelty = float(mgr.get("min_novelty", 0.35))

    by_id = {c.id: c for c in concerns}

    # pause/retire
    pause_ids = set(str(x) for x in (ops.get("pause") or [])[:3])
    retire_ids = set(str(x) for x in (ops.get("retire") or [])[:3])

    kept = []
    for c in concerns:
        if c.id in retire_ids:
            c.status = "done"
            archive.append({**asdict(c), "archived_ts": ts})
            continue
        if c.id in pause_ids and c.status == "active":
            c.status = "paused"
            c.activation = clamp(c.activation - 0.2, 0.0, 1.0)
        kept.append(c)

    # add at most 1
    adds = ops.get("add") or []
    if isinstance(adds, list) and adds:
        item = adds[0] if isinstance(adds[0], dict) else None
        if item:
            title = str(item.get("title", "")).strip()[:60]
            next_step = str(item.get("next_step", "")).strip()[:140]
            try:
                imp = float(item.get("importance", 0.6))
            except Exception:
                imp = 0.6
            cues = item.get("context_cues") or []
            if isinstance(cues, str):
                cues = [cues]
            cues = [str(x)[:30] for x in cues[:6]]

            if title and next_step:
                if concern_novel_enough(title, kept, min_novelty):
                    kept.append(CurrentConcern(
                        id=new_id("cc_ref_"),
                        title=title,
                        importance=clamp(imp, 0.2, 0.9),
                        progress=0.0,
                        activation=0.55,
                        next_step=next_step,
                        status="active",
                        context_cues=cues or [state.get("world", {}).get("location", "home_bedroom")],
                        source="reflection",
                        created_ts=ts,
                        ttl_min=1440.0
                    ))
                    mgr["cooldown_ticks"] = int(mgr.get("spawn_check_every_ticks", 10))

    state["concerns"] = [asdict(c) for c in kept]
    state["concern_archive"] = archive[-CONCERN_ARCHIVE_KEEP:]
    state["concern_manager"] = mgr

def apply_memory_ops(state: Dict[str, Any], ops: Dict[str, Any], ts: str):
    if not isinstance(ops, dict):
        return
    mem = state.get("memory", {})
    semantic = mem.get("semantic", []) or []

    # semantic_update
    su = ops.get("semantic_update") or []
    if isinstance(su, list):
        for item in su[:3]:
            if not isinstance(item, dict):
                continue
            claim = str(item.get("claim", "")).strip()
            if not claim:
                continue
            try:
                conf = float(item.get("confidence", 0.5))
            except Exception:
                conf = 0.5
            semantic.append({"ts": ts, "claim": claim[:200], "confidence": clamp(conf, 0.0, 1.0), "source": "self"})
    if len(semantic) > SEMANTIC_KEEP:
        semantic = semantic[-SEMANTIC_KEEP:]

    mem["semantic"] = semantic
    state["memory"] = mem

def apply_plan_queue(state: Dict[str, Any], plan: Dict[str, Any]):
    if not isinstance(plan, dict):
        return
    steps = plan.get("next_micro_steps")
    if not isinstance(steps, list):
        return
    queue = []
    for s in steps[:8]:
        if not isinstance(s, dict):
            continue
        tag = str(s.get("tag", "other")).strip()[:30] or "other"
        summary = str(s.get("summary", "")).strip()[:120]
        try:
            dur = float(s.get("duration_sec", 30))
        except Exception:
            dur = 30
        dur = clamp(dur, 1, TICK_SEC)
        if summary:
            queue.append({"tag": tag, "summary": summary, "duration_sec": int(dur)})
    # Replace plan_queue (simpler, avoids indefinite accumulation)
    if queue:
        state["plan_queue"] = queue

def apply_update(state: Dict[str, Any], model_json: Dict[str, Any], ts: str):
    # --- validate action ---
    action = model_json.get("action") or {}
    try:
        duration = float(action.get("duration_sec", TICK_SEC))
    except Exception:
        duration = TICK_SEC
    duration = clamp(duration, 1, TICK_SEC)

    a_type = str(action.get("type", "internal")).strip()
    if a_type not in ("internal", "physical", "social", "wait"):
        a_type = "internal"

    tag = str(action.get("tag", "other")).strip() or "other"
    summary = str(action.get("summary", "")).strip()[:240]
    if not summary:
        summary = "短暂整理注意并观察当前状态。"

    # --- apply world patch (validated) ---
    validate_world_patch(state, model_json.get("world_patch") or {})

    # --- objects ---
    needs = [dict_to_need(d) for d in state.get("needs", [])]
    concerns = [dict_to_concern(d) for d in state.get("concerns", [])]
    todos = [dict_to_todo(d) for d in state.get("todos", [])]

    # --- effects ---
    effects = model_json.get("effects") or {}
    progressed = False

    # need_delta (explicit)
    nd = effects.get("need_delta") or {}
    if isinstance(nd, dict):
        by_id = {n.id: n for n in needs}
        for nid, delta in nd.items():
            if nid in by_id:
                try:
                    by_id[nid].apply_delta(float(delta), satisfied=False, ts=ts)
                except Exception:
                    pass

    # apply tag heuristics (default physics)
    apply_tag_heuristics(needs, tag, ts)

    # concern progress
    cp = effects.get("concern_progress") or []
    if isinstance(cp, list):
        by_id = {c.id: c for c in concerns}
        for item in cp[:4]:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("id", "")).strip()
            if cid in by_id:
                try:
                    delta = float(item.get("delta", 0.0))
                except Exception:
                    delta = 0.0
                before = by_id[cid].progress
                if abs(delta) > 1e-6:
                    by_id[cid].apply_progress(delta, ts)
                    if by_id[cid].progress > before + 1e-6:
                        progressed = True

    # todo progress (lightweight)
    tp = effects.get("todo_progress") or []
    if isinstance(tp, list):
        by_id = {t.id: t for t in todos}
        for item in tp[:4]:
            if not isinstance(item, dict):
                continue
            tid = str(item.get("id", "")).strip()
            if tid in by_id and by_id[tid].status == "open":
                try:
                    delta = float(item.get("delta", 0.0))
                except Exception:
                    delta = 0.0
                if abs(delta) > 1e-6:
                    by_id[tid].progress = clamp(by_id[tid].progress + delta, 0.0, 1.0)
                    progressed = progressed or (delta > 0)
                    if by_id[tid].progress >= 0.999:
                        by_id[tid].status = "done"
                        by_id[tid].done_ts = ts

    # boredom delta
    guard = state.get("loop_guard", {})
    boredom = float(guard.get("boredom", 0.2))
    try:
        boredom_delta = float((effects.get("boredom_delta") if isinstance(effects, dict) else 0.0) or 0.0)
    except Exception:
        boredom_delta = 0.0
    boredom = clamp(boredom + boredom_delta, 0.0, 1.0)

    # repetition -> boredom up; novelty -> down
    last_tags = guard.get("last_tags", []) or []
    if last_tags and last_tags[-1] == tag:
        boredom = clamp(boredom + 0.02, 0.0, 1.0)
    else:
        boredom = clamp(boredom - 0.01, 0.0, 1.0)

    # loop guard counters
    last_tags.append(tag)
    if len(last_tags) > 10:
        last_tags = last_tags[-10:]
    repeat_last = sum(1 for x in last_tags if x == last_tags[-1]) if last_tags else 0

    no_progress_ticks = int(guard.get("no_progress_ticks", 0))
    if progressed:
        no_progress_ticks = 0
    else:
        no_progress_ticks += 1

    guard.update({
        "last_tags": last_tags,
        "repeat_last_tag_count": repeat_last,
        "no_progress_ticks": no_progress_ticks,
        "boredom": boredom
    })
    state["loop_guard"] = guard

    # --- memory (recent/episodic) ---
    memblk = model_json.get("memory") or {}
    recent_text = str(memblk.get("recent", "")).strip()[:360] or summary[:360]
    episodic_gist = str(memblk.get("episodic_gist", "")).strip()[:420] or f"{summary}（{ts}）"

    state["recent"] = state.get("recent", [])
    state["episodic"] = state.get("episodic", [])

    state["recent"].append({
        "ts": ts,
        "tag": tag,
        "text": f"[{a_type} {int(duration)}s] {recent_text}"
    })
    if len(state["recent"]) > RECENT_KEEP:
        state["recent"] = state["recent"][-RECENT_KEEP:]

    state["episodic"].append({"ts": ts, "gist": episodic_gist})
    if len(state["episodic"]) > EPISODIC_KEEP:
        overflow = state["episodic"][:-EPISODIC_KEEP]
        for item in overflow:
            append_jsonl(EPISODIC_ARCHIVE_JSONL, item)
        state["episodic"] = state["episodic"][-EPISODIC_KEEP:]

    # apply optional ops
    apply_todo_ops(state, model_json.get("todo_ops") or {}, ts)
    apply_concern_ops(state, model_json.get("concern_ops") or {}, ts)
    apply_memory_ops(state, model_json.get("memory_ops") or {}, ts)
    apply_plan_queue(state, model_json.get("plan") or {})

    # write back needs/concerns/todos
    state["needs"] = [asdict(n) for n in needs]
    state["concerns"] = [asdict(c) for c in concerns]
    state["todos"] = [asdict(t) for t in todos]

    state["last_action"] = {"ts": ts, "summary": summary, "type": a_type, "tag": tag, "duration_sec": duration}
    state["ts"] = ts


# ---------------- fallback when model fails ----------------

def fallback_action(state: Dict[str, Any], ts: str) -> Dict[str, Any]:
    # simple deterministic fallback: pick highest-urgency need, do a generic action
    needs = [dict_to_need(d) for d in state.get("needs", [])]
    needs.sort(key=lambda n: n.bid(), reverse=True)
    top = needs[0] if needs else None
    tag = "meta_replan"
    summary = "短暂停一下，重排下一步（避免空转）。"
    if top:
        if top.id in ("need_thirst",):
            tag, summary = "drink", "找杯子/拿起水杯，喝两口水。"
        elif top.id in ("need_hunger",):
            tag, summary = "eat", "去厨房方向挪一步，确认有什么能吃的。"
        elif top.id in ("need_bladder",):
            tag, summary = "toilet", "起身朝厕所方向走几步。"
        elif top.id in ("need_fatigue",):
            tag, summary = "rest", "坐稳/躺下，做三次缓慢呼吸。"
        elif top.id in ("need_order",):
            tag, summary = "tidy", "把眼前最碍事的一件东西归位。"
        else:
            tag, summary = "observe", "环顾四周10秒，确认下一步最容易推进的事。"

    return {
        "action": {"summary": summary, "duration_sec": 30, "type": "physical", "tag": tag},
        "effects": {"need_delta": {}, "concern_progress": [], "todo_progress": [], "boredom_delta": -0.02},
        "world_patch": {},
        "plan": {"horizon_min": 15, "next_micro_steps": [], "commitment": 0.2, "rationale": "模型失效时保持基本自稳"},
        "memory_ops": {"episodic_write": {"do": True, "gist": summary, "affect": {"valence": 0.0, "arousal": 0.2}, "links": []}},
        "todo_ops": {"add": [], "done": [], "update": []},
        "memory": {"recent": summary, "episodic_gist": f"{summary}（fallback）"},
        "meta": {"chosen_focus_ids": [], "conflict": "模型失效时优先自稳", "why_now": "保持连续性", "uncertainty": "LLM输出不可用"}
    }


# ---------------- main ----------------

def main():
    #api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    api_key = "用户自行填写"
    if not api_key:
        raise RuntimeError("Missing DEEPSEEK_API_KEY env var. Please export DEEPSEEK_API_KEY='...'(and do NOT hardcode).")

    client = OpenAI(api_key=api_key, base_url=BASE_URL)

    state = load_json(STATE_PATH, None)
    if not isinstance(state, dict):
        state = default_state()

    ensure_dir(STATE_DIR)

    print("Running 30s real-time loop (v2). Ctrl+C to stop.")
    while True:
        now = time.time()
        if ALIGN_TO_WALLCLOCK:
            t_next = next_tick_epoch(now, TICK_SEC)
            time.sleep(max(0.0, t_next - now))
        else:
            time.sleep(TICK_SEC)

        ts = now_iso_local()

        # deterministic dynamics -> stable attention competition even before LLM call
        tick_internal_dynamics(state, TICK_SEC, ts)

        messages = build_messages(state)
        
        try:
            resp = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                stream=False,
                extra_body={"thinking": {"type": "enabled"}},
            )
            print("after call")
            #print(messages)
            out = resp.choices[0].message.content or ""
            print(out)
            j = safe_extract_json(out)
        except Exception as e:
            # fallback: keep running, keep dynamics moving
            err = f"LLM失败或非JSON：{type(e).__name__}: {e}"
            state["recent"].append({"ts": ts, "tag": "llm_error", "text": err[:360]})
            if len(state["recent"]) > RECENT_KEEP:
                state["recent"] = state["recent"][-RECENT_KEEP:]
            j = fallback_action(state, ts)

        apply_update(state, j, ts)

        # persist
        save_json(STATE_PATH, state)

        la = state.get("last_action", {})
        guard = state.get("loop_guard", {})
        loc = state.get("world", {}).get("location", "")
        print(f"{ts} | loc={loc} | tag={la.get('tag')} rep={guard.get('repeat_last_tag_count',0)} np={guard.get('no_progress_ticks',0)} bor={float(guard.get('boredom',0.2)):.2f} | {la.get('summary')}")


if __name__ == "__main__":
    main()