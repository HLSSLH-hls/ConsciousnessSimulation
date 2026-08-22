"""
显现域 M(t₁,t₂) 与质性投影 Φ 的中介结构。

意识（理论定义）= 在维持活动 W 承载下的内在显现；
GW 点燃 ≈ access_global，不等于完整的 M。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.message import Message


@dataclass
class MaintenanceState:
    """宏大维持活动 W：体验基底场。"""

    brainstem_arousal: float = 0.6
    thalamic_gate: float = 0.85
    cortical_precision: float = 0.7
    fatigue: float = 0.0
    framework_coherence: float = 0.7

    @property
    def maintenance_level(self) -> float:
        gate = self.thalamic_gate
        arousal = self.brainstem_arousal
        fatigue_penalty = self.fatigue * 0.5
        return max(0.0, min(1.0, (gate * 0.5 + arousal * 0.35 + self.framework_coherence * 0.15) - fatigue_penalty))

    @property
    def can_host_manifestation(self) -> bool:
        return self.maintenance_level > 0.35

    def to_dict(self) -> Dict[str, Any]:
        return {
            "brainstem_arousal": round(self.brainstem_arousal, 4),
            "thalamic_gate": round(self.thalamic_gate, 4),
            "cortical_precision": round(self.cortical_precision, 4),
            "fatigue": round(self.fatigue, 4),
            "framework_coherence": round(self.framework_coherence, 4),
            "maintenance_level": round(self.maintenance_level, 4),
            "can_host_manifestation": self.can_host_manifestation,
        }


@dataclass
class AttentionItem:
    content: Dict[str, Any]
    weight: float
    source_kind: str
    layer: str  # foreground | background | fringe


@dataclass
class ManifestationDomain:
    """单 tick 采样 + specious present 窗内的显现域快照。"""

    tick: int
    t_span: Tuple[int, int]
    W: MaintenanceState
    access_global: bool = False
    access_score: float = 0.0
    access_recurrent: bool = False
    manifest_level: float = 0.0
    has_conscious_manifestation: bool = False
    slots: Dict[str, Any] = field(default_factory=dict)
    attention_layout: Dict[str, List[Dict[str, Any]]] = field(default_factory=dict)
    binding: Dict[str, Any] = field(default_factory=dict)
    phi_projection: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tick": self.tick,
            "t_span": list(self.t_span),
            "W": self.W.to_dict(),
            "access": {
                "global_access": self.access_global,
                "access_score": round(self.access_score, 4),
                "recurrent": self.access_recurrent,
            },
            "manifest": {
                "manifest_level": round(self.manifest_level, 4),
                "has_conscious_manifestation": self.has_conscious_manifestation,
            },
            "slots": self.slots,
            "attention_layout": self.attention_layout,
            "binding": self.binding,
            "phi_projection": self.phi_projection,
        }


class ManifestationBuilder:
    """从 tick 内消息构造 M；Q 为可学习的质性渲染占位。"""

    def __init__(
        self,
        specious_present_ticks: int = 5,
        manifest_epsilon: float = 0.12,
        w_min_host: float = 0.35,
    ) -> None:
        self.specious_present_ticks = specious_present_ticks
        self.manifest_epsilon = manifest_epsilon
        self.w_min_host = w_min_host
        self._window: List[ManifestationDomain] = []
        self._tick_start: int = 0

    @property
    def current_window(self) -> List[ManifestationDomain]:
        return list(self._window)

    def _parse_W(self, incoming: List[Message]) -> MaintenanceState:
        w = MaintenanceState()
        for m in incoming:
            if m.kind != "modulation":
                continue
            p = m.payload
            w.brainstem_arousal = float(p.get("brainstem_arousal", w.brainstem_arousal))
            w.thalamic_gate = float(p.get("thalamic_gate", w.thalamic_gate))
            w.cortical_precision = float(p.get("cortical_precision", w.cortical_precision))
            w.fatigue = float(p.get("fatigue", w.fatigue))
        w.framework_coherence = max(
            0.2,
            min(1.0, w.thalamic_gate * 0.4 + w.cortical_precision * 0.35 + (1.0 - w.fatigue) * 0.25),
        )
        return w

    def _ambient_perceptual_fields(self, gate: float) -> List[Dict[str, Any]]:
        """正常人清醒时各模态以低权重持续在场（背景场）。"""
        base = 0.18 * gate
        return [
            {"modality": "visual", "content": "ambient_visual_field", "spatial": "egocentric", "weight": base},
            {"modality": "auditory", "content": "ambient_auditory_field", "spatial": None, "weight": base * 0.85},
            {"modality": "proprioceptive", "content": "body_posture_sense", "spatial": "body", "weight": base * 0.9},
            {"modality": "interoceptive", "content": "baseline_interoception", "spatial": "body", "weight": base * 0.7},
        ]

    def _build_affect_layer(self, incoming: List[Message]) -> Dict[str, Any]:
        affect_msgs = [m for m in incoming if m.kind == "affect"]
        if not affect_msgs:
            return {
                "core_tone": "neutral",
                "valence": 0.0,
                "arousal": 0.15,
                "layer_role": "background",
                "interoceptive_burst": 0.0,
                "volatility_index": None,
            }
        p = affect_msgs[-1].payload
        v = float(p.get("valence", 0))
        a = float(p.get("arousal", 0.2))
        tone = p.get("background_tone") or p.get("core_tone") or "neutral"
        return {
            "core_tone": tone,
            "valence": v,
            "arousal": a,
            "layer_role": "background",
            "interoceptive_burst": float(p.get("interoceptive_burst", abs(v) * a * 0.5)),
            "volatility_index": p.get("volatility_index"),
            "stability": p.get("stability"),
        }

    def _build_self_frame(self, incoming: List[Message], goals_state: Optional[List[Dict]]) -> Dict[str, Any]:
        goals = goals_state or []
        active = [g for g in goals if g.get("active")]
        return {
            "egocentric_perspective": True,
            "body_ownership": 0.85,
            "presence": 0.7,
            "narrative_self": [g.get("text", "") for g in active[:3]],
            "meaning_center": "self_relevance",
        }

    def _build_cognitive_stream(
        self, access_global: bool, coalition: Optional[Dict], manifest_level: float
    ) -> Dict[str, Any]:
        fragments = []
        if coalition:
            primary = coalition.get("primary") or {}
            concepts = (primary.get("features") or {}).get("concepts", [])
            if concepts:
                fragments.append({"type": "concept", "content": concepts[0]})
        fluency = 0.5 + manifest_level * 0.3
        if access_global:
            fragments.append({"type": "metacognitive", "content": "noticed_in_experience"})
        return {
            "fragments": fragments,
            "fluency": round(fluency, 3),
            "inner_speech_placeholder": None,
        }

    def _rank_attention_items(self, incoming: List[Message], modulation: MaintenanceState) -> List[AttentionItem]:
        items: List[AttentionItem] = []
        for m in incoming:
            if m.kind == "percept":
                items.append(
                    AttentionItem(
                        content={"features": m.payload.get("features"), "modality": m.payload.get("features", {}).get("modality", "synthetic")},
                        weight=m.salience * modulation.thalamic_gate,
                        source_kind="percept",
                        layer="",
                    )
                )
            elif m.kind == "memory_hit":
                items.append(
                    AttentionItem(
                        content={"memory": m.payload},
                        weight=m.salience * 0.85,
                        source_kind="memory_hit",
                        layer="",
                    )
                )
            elif m.kind == "goal_bias":
                items.append(
                    AttentionItem(
                        content={"goal": m.payload},
                        weight=float(m.payload.get("boost", 0.3)),
                        source_kind="goal_bias",
                        layer="",
                    )
                )

        items.sort(key=lambda x: x.weight, reverse=True)
        if not items:
            return items

        top = items[0].weight
        for it in items:
            if it.weight >= top * 0.72:
                it.layer = "foreground"
            elif it.weight >= top * 0.35:
                it.layer = "background"
            else:
                it.layer = "fringe"
        return items

    def _layout_from_items(self, items: List[AttentionItem]) -> Dict[str, List[Dict[str, Any]]]:
        layout: Dict[str, List[Dict[str, Any]]] = {"foreground": [], "background": [], "fringe": []}
        for it in items:
            layout[it.layer].append(
                {"weight": round(it.weight, 4), "source": it.source_kind, "content": it.content}
            )
        return layout

    def _compute_manifest_level(
        self,
        W: MaintenanceState,
        layout: Dict[str, List[Dict]],
        affect: Dict[str, Any],
        access_global: bool,
        access_score: float,
    ) -> float:
        fg = sum(x.get("weight", 0) for x in layout.get("foreground", []))
        bg = sum(x.get("weight", 0) for x in layout.get("background", []))
        fr = sum(x.get("weight", 0) for x in layout.get("fringe", []))
        ambient = 0.25 * W.thalamic_gate
        affect_contrib = abs(affect.get("valence", 0)) * 0.15 + affect.get("arousal", 0) * 0.2
        level = ambient + fg * 0.45 + bg * 0.22 + fr * 0.1 + affect_contrib
        if access_global:
            level += 0.15 + access_score * 0.2
        return max(0.0, min(1.0, level * W.maintenance_level))

    def _render_phi(self, M: ManifestationDomain) -> Dict[str, Any]:
        """Q: M → Φ 占位。ALGO: 用主观报告/生理数据学习映射。"""
        aff = M.slots.get("affect_layer") or {}
        fg = M.attention_layout.get("foreground") or []
        tone = aff.get("core_tone", "neutral")
        vividness = M.manifest_level
        bodily = float(aff.get("interoceptive_burst", 0))
        presence = (M.slots.get("self_frame") or {}).get("presence", 0.5)
        return {
            "affective_quality": tone,
            "vividness": round(vividness, 4),
            "bodily_feeling_strength": round(bodily, 4),
            "self_presence_quality": round(presence * vividness, 4),
            "foreground_unity": min(1.0, len(fg) * 0.3 + (0.4 if M.access_global else 0.1)),
        }

    def build(
        self,
        tick: int,
        incoming: List[Message],
        goals_state: Optional[List[Dict]] = None,
    ) -> ManifestationDomain:
        W = self._parse_W(incoming)
        access_global = False
        access_score = 0.0
        access_recurrent = False
        coalition = None

        for m in incoming:
            if m.kind == "gw_broadcast":
                access_global = bool(m.payload.get("ignited"))
                access_recurrent = bool(m.payload.get("recurrent"))
                winner = m.payload.get("winner") or {}
                coalition = winner.get("coalition") or winner
                access_score = m.salience
            elif m.kind == "gw_subthreshold" and coalition is None:
                coalition = m.payload.get("coalition")
                access_score = float(m.payload.get("score", 0)) * 0.5

        affect_layer = self._build_affect_layer(incoming)
        items = self._rank_attention_items(incoming, W)
        layout = self._layout_from_items(items)

        perceptual = self._ambient_perceptual_fields(W.thalamic_gate)
        for m in incoming:
            if m.kind != "percept":
                continue
            feat = m.payload.get("features") or {}
            modality = feat.get("modality", "synthetic")
            concepts = feat.get("concepts", [])
            perceptual.append(
                {
                    "modality": modality,
                    "content": concepts[0] if concepts else "percept",
                    "confidence": feat.get("confidence"),
                    "spatial": "egocentric",
                    "weight": m.salience,
                }
            )

        manifest_level = self._compute_manifest_level(W, layout, affect_layer, access_global, access_score)
        has_manifest = W.can_host_manifestation and manifest_level >= self.manifest_epsilon

        if affect_layer.get("arousal", 0) > 0.55 and manifest_level > 0.2:
            affect_layer["layer_role"] = "foreground"
        elif manifest_level < 0.25:
            affect_layer["layer_role"] = "background"

        M = ManifestationDomain(
            tick=tick,
            t_span=(self._tick_start, tick),
            W=W,
            access_global=access_global,
            access_score=access_score,
            access_recurrent=access_recurrent,
            manifest_level=manifest_level,
            has_conscious_manifestation=has_manifest,
            slots={
                "perceptual_fields": perceptual,
                "affect_layer": affect_layer,
                "cognitive_stream": self._build_cognitive_stream(access_global, coalition, manifest_level),
                "self_frame": self._build_self_frame(incoming, goals_state),
            },
            attention_layout=layout,
            binding={
                "coalition": coalition,
                "generative_memory_cues": [
                    x["content"].get("memory") for x in layout.get("fringe", []) + layout.get("background", [])
                    if x.get("source") == "memory_hit"
                ][:3],
            },
        )
        M.phi_projection = self._render_phi(M)

        self._window.append(M)
        if len(self._window) > self.specious_present_ticks:
            self._window = self._window[-self.specious_present_ticks :]
        if len(self._window) == 1:
            self._tick_start = tick
        M.t_span = (self._tick_start, tick)

        return M

    def window_summary(self) -> Dict[str, Any]:
        """Specious present：窗内整合。"""
        if not self._window:
            return {}
        levels = [m.manifest_level for m in self._window]
        return {
            "t_span": [self._window[0].tick, self._window[-1].tick],
            "mean_manifest_level": round(sum(levels) / len(levels), 4),
            "peak_manifest_level": round(max(levels), 4),
            "any_access_global": any(m.access_global for m in self._window),
            "samples": len(self._window),
        }
