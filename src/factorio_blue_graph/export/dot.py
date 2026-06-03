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


def _merge_block(
    merge_id: str,
    item_label: str,
    total_rate: float,
    inbound: list[tuple[str, float]],
    outbound: list[tuple[str, float]],
) -> tuple[str, list[str]]:
    """Build a belt-merge node plus its thin in/out edges.

    Individual machines feed a trickle into the merge point and consumers tap a
    trickle back out, so the in/out edges keep their honest per-machine rates.
    The *consolidation* is shown on the node itself: a hexagon filled with the
    belt-tier colour for the summed `total_rate`, labelled with the belt tier
    and lane count that flow actually needs. This is what turns "125 machines
    each on a near-empty yellow belt" into "1 fully-loaded yellow belt".
    """
    belt = pick_tier(total_rate)
    color = _TIER_COLOR[belt.tier]
    tier_name = belt.tier.name.lower()
    node_label = f"{item_label}\\n{total_rate:.2f}/s\\n{belt.lane_count}× {tier_name} merge"
    node = (
        f"  {_q(merge_id)} [label={_q(node_label)}, "
        f"shape=hexagon, style=filled, fillcolor={color}];"
    )
    edges = [_edge_line(src, merge_id, item_label, rate) for src, rate in inbound]
    edges += [_edge_line(merge_id, dst, item_label, rate) for dst, rate in outbound]
    return node, edges


def _inter_machine_merges(
    flow_graph: FlowGraph,
    machine_plan: MachinePlan,
    recipe_graph: RecipeHypergraph,
) -> tuple[list[str], list[str]]:
    """One merge node per `ItemChannel`, replacing the bipartite edge set.

    The summed inbound trickles equal `channel.rate` exactly, so the merge
    node's belt assignment matches Phase 3's own `assign_tiers` for the channel.
    """
    node_lines: list[str] = []
    edge_lines: list[str] = []
    for ch in flow_graph.channels:
        prod_count = machine_plan.machine_counts.get(ch.producer_recipe, 0)
        cons_count = machine_plan.machine_counts.get(ch.consumer_recipe, 0)
        if prod_count <= 0 or cons_count <= 0:
            continue
        merge_id = f"merge:{ch.item_id}:{ch.producer_recipe}->{ch.consumer_recipe}"
        item_label = _item_name(recipe_graph, ch.item_id)
        inbound = [(f"{ch.producer_recipe}#{i}", ch.rate / prod_count) for i in range(prod_count)]
        outbound = [(f"{ch.consumer_recipe}#{j}", ch.rate / cons_count) for j in range(cons_count)]
        node, edges = _merge_block(merge_id, item_label, ch.rate, inbound, outbound)
        node_lines.append(node)
        edge_lines.extend(edges)
    return node_lines, edge_lines


def _raw_input_merges(
    flow_graph: FlowGraph,
    recipe_graph: RecipeHypergraph,
) -> tuple[list[str], list[str]]:
    """One merge node per (raw item, consumer recipe) consolidating ore feeds."""
    groups: dict[tuple[str, str], list[tuple[str, float]]] = {}
    for ext in flow_graph.external_in_edges:
        consumer_recipe = ext.consumer_node.rsplit("#", 1)[0]
        groups.setdefault((ext.item_id, consumer_recipe), []).append((ext.consumer_node, ext.rate))

    node_lines: list[str] = []
    edge_lines: list[str] = []
    for (item_id, consumer_recipe), consumers in groups.items():
        total = sum(rate for _, rate in consumers)
        merge_id = f"merge:raw:{item_id}->{consumer_recipe}"
        item_label = _item_name(recipe_graph, item_id)
        inbound = [("raw:" + item_id, total)]
        node, edges = _merge_block(merge_id, item_label, total, inbound, consumers)
        node_lines.append(node)
        edge_lines.extend(edges)
    return node_lines, edge_lines


def _output_merges(
    demand: DemandPlan,
    machine_plan: MachinePlan,
    recipe_graph: RecipeHypergraph,
) -> tuple[list[str], list[str]]:
    """A single merge node consolidating every target machine into the sink."""
    target = demand.target_item
    target_count = machine_plan.machine_counts.get(target, 0)
    if target_count <= 0:
        return [], []
    total = demand.target_rate_per_sec
    per_machine = total / target_count
    merge_id = f"merge:out:{target}"
    item_label = _item_name(recipe_graph, target)
    inbound = [(f"{target}#{i}", per_machine) for i in range(target_count)]
    node, edges = _merge_block(merge_id, item_label, total, inbound, [(_OUTPUT_NODE, total)])
    return [node], edges


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

    # --- Belt-merge nodes consolidating trickles into loaded belts ---------
    # Each group inserts a merge node between many under-utilised per-machine
    # edges and the consumers, so the graph shows real belt loads rather than
    # one near-empty belt per assembler.
    raw_nodes, raw_edges = _raw_input_merges(flow_graph, recipe_graph)
    inter_nodes, inter_edges = _inter_machine_merges(flow_graph, machine_plan, recipe_graph)
    out_nodes, out_edges = _output_merges(demand, machine_plan, recipe_graph)
    lines.extend(raw_nodes)
    lines.extend(inter_nodes)
    lines.extend(out_nodes)

    lines.append("")

    lines.extend(raw_edges)
    lines.extend(inter_edges)
    lines.extend(out_edges)

    lines.append("}")
    return "\n".join(lines) + "\n"


__all__ = ["to_dot"]
