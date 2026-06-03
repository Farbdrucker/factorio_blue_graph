"""Graphviz `.dot` export of the per-machine flow graph.

Renders the Phase 1–3 flow model as a Graphviz directed graph: one node per
concrete machine instance (`FlowGraph.nodes`), plus a node per distinct raw /
ore input and a single output sink for the target item. Each edge is labelled
with the item name, flow rate, belt lane count, and belt tier, and coloured by
tier so the graph can be eyeballed for bottlenecks.

This is a human-inspection aid only — it does not produce a blueprint string.
The output is plain `.dot` text; callers may render it with the Graphviz `dot`
binary if installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from factorio_blue_graph.layout.tier import BeltTier, pick_tier

if TYPE_CHECKING:
    from factorio_blue_graph.model.graph import FlowGraph, RecipeHypergraph
    from factorio_blue_graph.planning.demand import DemandPlan
    from factorio_blue_graph.planning.lp import MachinePlan

# Belt-tier → edge colour (Graphviz X11 names).
_TIER_COLOR: dict[BeltTier, str] = {
    BeltTier.YELLOW: "gold",
    BeltTier.RED: "red",
    BeltTier.BLUE: "dodgerblue",
}

_OUTPUT_NODE = "__output__"


def _q(s: str) -> str:
    """Quote and escape a string for use as a DOT id or label."""
    return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _item_name(recipe_graph: RecipeHypergraph, item_id: str) -> str:
    item = recipe_graph.items.get(item_id)
    return item.name if item is not None else item_id


def _edge_line(src: str, dst: str, item_label: str, rate: float) -> str:
    """One DOT edge annotated with item, rate, lanes, and belt tier."""
    belt = pick_tier(rate)
    color = _TIER_COLOR[belt.tier]
    tier_name = belt.tier.name.lower()
    label = f"{item_label}\\n{rate:.2f}/s\\n{belt.lane_count}× {tier_name}"
    penwidth = 1.0 + (belt.lane_count - 1)
    return (
        f"  {_q(src)} -> {_q(dst)} ["
        f"label={_q(label)}, color={color}, fontcolor={color}, "
        f"penwidth={penwidth:.1f}];"
    )


def to_dot(
    demand: DemandPlan,
    machine_plan: MachinePlan,
    flow_graph: FlowGraph,
    recipe_graph: RecipeHypergraph,
    *,
    label: str | None = None,
) -> str:
    """Build a Graphviz `.dot` description of the per-machine flow graph."""
    lines: list[str] = ["digraph factory {", "  rankdir=LR;", "  node [fontname=Helvetica];"]
    if label:
        lines.append(f"  label={_q(label + ' flow graph')};")
        lines.append("  labelloc=t;")

    # --- Machine nodes (one per concrete machine instance) -----------------
    for node_id, node in flow_graph.nodes.items():
        recipe_name = _item_name(recipe_graph, node.recipe_id)
        node_label = f"{recipe_name}\\n[{node.machine.id}]\\n{node.crafts_per_sec:.2f} craft/s"
        lines.append(
            f"  {_q(node_id)} [label={_q(node_label)}, shape=box, "
            f"style=filled, fillcolor=lightsteelblue];"
        )

    # --- Ore / raw-input nodes (one per distinct raw item) -----------------
    raw_items = {ext.item_id for ext in flow_graph.external_in_edges}
    for item_id in sorted(raw_items):
        node_label = f"{_item_name(recipe_graph, item_id)}\\n(raw)"
        lines.append(
            f"  {_q('raw:' + item_id)} [label={_q(node_label)}, shape=cylinder, "
            f"style=filled, fillcolor=peru];"
        )

    # --- Output sink for the target item -----------------------------------
    target_name = _item_name(recipe_graph, demand.target_item)
    output_label = _q(target_name + "\\noutput")
    lines.append(
        f"  {_q(_OUTPUT_NODE)} [label={output_label}, "
        f"shape=doublecircle, style=filled, fillcolor=palegreen];"
    )

    lines.append("")

    # --- Raw-input edges: ore node -> consumer machine ---------------------
    for ext in flow_graph.external_in_edges:
        lines.append(
            _edge_line(
                "raw:" + ext.item_id,
                ext.consumer_node,
                _item_name(recipe_graph, ext.item_id),
                ext.rate,
            )
        )

    # --- Inter-machine edges from the flow MultiDiGraph --------------------
    for src, dst, data in flow_graph.graph.edges(data=True):
        lines.append(_edge_line(src, dst, _item_name(recipe_graph, data["item"]), data["rate"]))

    # --- Output edges: target machines -> output sink ----------------------
    target = demand.target_item
    target_count = machine_plan.machine_counts.get(target, 0)
    if target_count > 0:
        per_machine = demand.target_rate_per_sec / target_count
        for i in range(target_count):
            lines.append(_edge_line(f"{target}#{i}", _OUTPUT_NODE, target_name, per_machine))

    lines.append("}")
    return "\n".join(lines) + "\n"


__all__ = ["to_dot"]
