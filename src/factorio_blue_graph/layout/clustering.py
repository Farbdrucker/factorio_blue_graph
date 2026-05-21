"""Phase 4: cluster the per-machine `FlowGraph` into `Block`s.

Two-pass strategy honouring the spec preference for single-recipe blocks
under a hard `max_block_size` cap:

1. **Recipe-local chunking.** Group machine nodes by `recipe_id`. A recipe
   with `k ≤ max_block_size` machines yields one proto-block; larger
   recipes split into `ceil(k / max_block_size)` near-equal chunks. Sibling
   instances of the same recipe carry no edges in the bipartite flow graph
   (they share neighbours, not connections), so any deterministic split is
   modularity-equivalent — we use `MachineNode.id` order.
2. **Louvain merge of tightly-coupled proto-blocks.** Build an undirected
   weighted graph whose nodes are proto-blocks and whose edge weights sum
   `FlowGraph` rates spanning the pair. Run Louvain (`community.best_partition`
   with a fixed `random_state` for determinism); within each community,
   pack proto-blocks first-fit-decreasing into final blocks of capacity
   `max_block_size`. A community whose total size exceeds the cap stays
   split into multiple final blocks; a community small enough fuses into
   a single mixed-recipe block — this is where "tightly coupled" mixing
   shows up.
"""

from __future__ import annotations

import math
from collections import Counter

import community as community_louvain
import networkx as nx

from factorio_blue_graph.model.graph import (
    Block,
    BlockEdge,
    BlockGraph,
    FlowGraph,
    MachineNode,
)

DEFAULT_MAX_BLOCK_SIZE = 16
BLOCK_MARGIN = 2  # tiles of belt margin on each side, per spec


def cluster_into_blocks(
    flow_graph: FlowGraph,
    max_block_size: int = DEFAULT_MAX_BLOCK_SIZE,
    random_state: int = 42,
) -> BlockGraph:
    """Cluster `flow_graph`'s machines into a `BlockGraph`.

    `max_block_size` is the hard cap on machines per block (default 16).
    `random_state` is forwarded to Louvain for reproducible partitions.
    """
    if max_block_size <= 0:
        raise ValueError(f"max_block_size must be positive, got {max_block_size}")

    bg = BlockGraph()
    if flow_graph.total_machines == 0:
        return bg

    proto_chunks = _recipe_chunks(flow_graph, max_block_size)
    meta = _proto_meta_graph(proto_chunks, flow_graph)
    partition = community_louvain.best_partition(meta, weight="weight", random_state=random_state)
    final_groups = _pack_communities(proto_chunks, partition, max_block_size)

    member_to_block: dict[str, str] = {}
    for idx, members in enumerate(final_groups):
        block = _build_block(idx, members)
        bg.add_block(block)
        for m in members:
            member_to_block[m.id] = block.id

    for edge in _aggregate_block_edges(flow_graph, member_to_block):
        bg.add_edge(edge)

    return bg


def _recipe_chunks(flow_graph: FlowGraph, max_block_size: int) -> list[list[MachineNode]]:
    by_recipe: dict[str, list[MachineNode]] = {}
    for node in flow_graph.nodes.values():
        by_recipe.setdefault(node.recipe_id, []).append(node)

    chunks: list[list[MachineNode]] = []
    for recipe_id in sorted(by_recipe):
        nodes = sorted(by_recipe[recipe_id], key=lambda n: n.id)
        chunks.extend(_split_evenly(nodes, max_block_size))
    return chunks


def _split_evenly(nodes: list[MachineNode], max_size: int) -> list[list[MachineNode]]:
    n = len(nodes)
    if n <= max_size:
        return [nodes]
    num_chunks = math.ceil(n / max_size)
    base, extra = divmod(n, num_chunks)
    out: list[list[MachineNode]] = []
    idx = 0
    for i in range(num_chunks):
        size = base + (1 if i < extra else 0)
        out.append(nodes[idx : idx + size])
        idx += size
    return out


def _proto_meta_graph(chunks: list[list[MachineNode]], flow_graph: FlowGraph) -> nx.Graph:
    g = nx.Graph()
    node_to_chunk: dict[str, int] = {}
    for idx, members in enumerate(chunks):
        g.add_node(idx)
        for m in members:
            node_to_chunk[m.id] = idx

    for u, v, data in flow_graph.graph.edges(data=True):
        cu = node_to_chunk[u]
        cv = node_to_chunk[v]
        if cu == cv:
            continue
        a, b = (cu, cv) if cu < cv else (cv, cu)
        rate = data.get("rate", 0.0)
        if g.has_edge(a, b):
            g[a][b]["weight"] += rate
        else:
            g.add_edge(a, b, weight=rate)
    return g


def _pack_communities(
    chunks: list[list[MachineNode]],
    partition: dict[int, int],
    max_block_size: int,
) -> list[list[MachineNode]]:
    by_community: dict[int, list[int]] = {}
    for chunk_idx, comm in partition.items():
        by_community.setdefault(comm, []).append(chunk_idx)

    final: list[list[MachineNode]] = []
    for comm in sorted(by_community):
        members = sorted(by_community[comm], key=lambda i: (-len(chunks[i]), i))
        bins: list[list[MachineNode]] = []
        bin_sizes: list[int] = []
        for chunk_idx in members:
            chunk = chunks[chunk_idx]
            placed = False
            for bi, bsize in enumerate(bin_sizes):
                if bsize + len(chunk) <= max_block_size:
                    bins[bi].extend(chunk)
                    bin_sizes[bi] = bsize + len(chunk)
                    placed = True
                    break
            if not placed:
                bins.append(list(chunk))
                bin_sizes.append(len(chunk))
        final.extend(bins)
    return final


def _build_block(idx: int, members: list[MachineNode]) -> Block:
    block_id = f"block-{idx}"
    sorted_members = tuple(sorted(members, key=lambda n: n.id))
    recipe_counter = Counter(m.recipe_id for m in sorted_members)
    recipes = tuple(sorted(recipe_counter))
    primary_recipe = min(recipe_counter.items(), key=lambda kv: (-kv[1], kv[0]))[0]

    k = len(sorted_members)
    side = math.ceil(math.sqrt(k))
    mw = max(m.machine.footprint[0] for m in sorted_members)
    mh = max(m.machine.footprint[1] for m in sorted_members)
    footprint = (side * mw + 2 * BLOCK_MARGIN, side * mh + 2 * BLOCK_MARGIN)

    return Block(
        id=block_id,
        members=sorted_members,
        recipes=recipes,
        footprint=footprint,
        primary_recipe=primary_recipe,
    )


def _aggregate_block_edges(
    flow_graph: FlowGraph, member_to_block: dict[str, str]
) -> list[BlockEdge]:
    sums: dict[tuple[str, str, str], float] = {}
    for u, v, data in flow_graph.graph.edges(data=True):
        bu = member_to_block[u]
        bv = member_to_block[v]
        if bu == bv:
            continue
        key = (bu, bv, data["item"])
        sums[key] = sums.get(key, 0.0) + data["rate"]

    return [
        BlockEdge(item_id=item, source_block=bu, target_block=bv, rate=rate)
        for (bu, bv, item), rate in sorted(sums.items())
    ]


__all__ = [
    "BLOCK_MARGIN",
    "DEFAULT_MAX_BLOCK_SIZE",
    "cluster_into_blocks",
]
