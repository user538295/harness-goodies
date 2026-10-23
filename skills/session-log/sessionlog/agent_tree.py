"""Pure nesting of the sub-agents of one session, shared by the harness readers: who is whose parent, how deep, and the tree
built deepest first; no file access."""
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from .model import SubAgent

UNKNOWN_TYPE = "unknown"


def _is_in_parent_loop(start: int, parent_of: Dict[int, int]) -> bool:
    """Whether following the parents from `start` comes back to `start`."""
    seen = {start}
    node = parent_of.get(start)
    while node is not None:
        if node == start:
            return True
        if node in seen:
            return False
        seen.add(node)
        node = parent_of.get(node)
    return False


def parent_indexes(ids: Sequence[str], parent_ids: Sequence[Optional[str]]) -> Dict[int, int]:
    """Index of each agent's parent agent, from the agents' own ids and the ids of their parents (same order). Of two agents
    with one id the first is the parent. A parent id that names no agent, the agent itself, and every agent in a parent loop
    are left out: such an agent sits at the top level, so no agent and none of its tokens is lost."""
    first_index: Dict[str, int] = {}
    for index, agent_id in enumerate(ids):
        first_index.setdefault(agent_id, index)
    parent_of: Dict[int, int] = {}
    for index, parent_id in enumerate(parent_ids):
        parent = first_index.get(parent_id) if parent_id else None
        if parent is not None:
            parent_of[index] = parent
    for index in [index for index in parent_of if _is_in_parent_loop(index, parent_of)]:
        del parent_of[index]
    return parent_of


def depths_of(parent_of: Dict[int, int], count: int) -> List[int]:
    """The depth of each of `count` agents: the number of ancestors it has, 0 for a top-level agent. `parent_of` has no loop."""
    depths = []
    for index in range(count):
        depth, node = 0, parent_of.get(index)
        while node is not None:
            depth += 1
            node = parent_of.get(node)
        depths.append(depth)
    return depths


def build_tree(parent_of: Dict[int, int], depths: Sequence[int],
               build: Callable[[int, Tuple[SubAgent, ...]], SubAgent]) -> Tuple[Tuple[int, SubAgent], ...]:
    """The top-level agents of the `len(depths)` agents (`depths` is `depths_of(parent_of, count)`), each with its index and
    holding its children. `build(index, children)` makes the agent at `index` from the children already built; the deepest
    agents are built first, so a parent holds its finished children. The top-level agents come in index order."""
    count = len(depths)
    children: Dict[int, List[SubAgent]] = {index: [] for index in range(count)}
    top_level: List[Tuple[int, SubAgent]] = []
    for index in sorted(range(count), key=lambda index: -depths[index]):
        built = build(index, tuple(children[index]))
        if index in parent_of:
            children[parent_of[index]].append(built)
        else:
            top_level.append((index, built))
    return tuple(top_level)
