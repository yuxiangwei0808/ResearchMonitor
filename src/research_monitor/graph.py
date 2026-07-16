from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass

from .models import Pipeline, Task, TaskEdge


@dataclass(frozen=True)
class GraphArc:
    source_id: str
    target_id: str
    kind: str
    edge_id: str | None = None
    waived: bool = False


class GraphCycleError(ValueError):
    def __init__(self, path: list[str]):
        self.path = path
        super().__init__("dependency cycle: " + " -> ".join(path))


def descendants(tasks: list[Task]) -> dict[str, set[str]]:
    children: dict[str, list[str]] = defaultdict(list)
    for task in tasks:
        if task.parent_id:
            children[task.parent_id].append(task.id)
    result: dict[str, set[str]] = {}
    for task in tasks:
        found: set[str] = set()
        stack = list(children.get(task.id, []))
        while stack:
            current = stack.pop()
            if current in found:
                continue
            found.add(current)
            stack.extend(children.get(current, []))
        result[task.id] = found
    return result


def derived_sequence_arcs(tasks: list[Task], pipelines: list[Pipeline]) -> list[GraphArc]:
    pipeline_modes = {p.id: p.flow_mode for p in pipelines if p.deleted_at is None}
    parent_modes = {t.id: t.child_flow_mode for t in tasks if t.deleted_at is None}
    grouped: dict[tuple[str, str | None], list[Task]] = defaultdict(list)
    for task in tasks:
        if task.deleted_at is None and task.status != "dropped":
            grouped[(task.pipeline_id, task.parent_id)].append(task)

    arcs: list[GraphArc] = []
    for (pipeline_id, parent_id), siblings in grouped.items():
        mode = pipeline_modes.get(pipeline_id, "freeform") if parent_id is None else parent_modes.get(parent_id, "freeform")
        if mode != "sequential":
            continue
        ordered = sorted(siblings, key=lambda item: (item.order_index, item.created_at, item.id))
        arcs.extend(
            GraphArc(left.id, right.id, "sequence")
            for left, right in zip(ordered, ordered[1:])
        )
    return arcs


def explicit_dependency_arcs(edges: list[TaskEdge]) -> list[GraphArc]:
    return [
        GraphArc(edge.source_id, edge.target_id, "dependency", edge.id, bool(edge.waived_reason))
        for edge in edges
        if edge.deleted_at is None and edge.enabled and edge.edge_type == "dependency"
    ]


def all_arcs(tasks: list[Task], pipelines: list[Pipeline], edges: list[TaskEdge]) -> list[GraphArc]:
    return derived_sequence_arcs(tasks, pipelines) + explicit_dependency_arcs(edges)


def expanded_arcs(
    tasks: list[Task], pipelines: list[Pipeline], edges: list[TaskEdge]
) -> list[GraphArc]:
    """Expand a prerequisite targeting a parent across its complete subtree.

    Readiness and cycle validation must see the same graph.  In particular, a
    descendant cannot be made a prerequisite of one of its ancestors because
    expansion would make it a prerequisite of itself.
    """
    active_tasks = [task for task in tasks if task.deleted_at is None]
    active_ids = {task.id for task in active_tasks}
    desc = descendants(active_tasks)
    expanded: list[GraphArc] = []
    for arc in all_arcs(active_tasks, pipelines, edges):
        if arc.source_id not in active_ids or arc.target_id not in active_ids:
            continue
        for target_id in {arc.target_id, *desc.get(arc.target_id, set())}:
            expanded.append(
                GraphArc(
                    source_id=arc.source_id,
                    target_id=target_id,
                    kind=arc.kind,
                    edge_id=arc.edge_id,
                    waived=arc.waived,
                )
            )
    return expanded


def validate_dag(tasks: list[Task], pipelines: list[Pipeline], edges: list[TaskEdge]) -> None:
    active = {t.id for t in tasks if t.deleted_at is None}
    adjacency: dict[str, list[str]] = defaultdict(list)
    for arc in expanded_arcs(tasks, pipelines, edges):
        if arc.waived:
            continue
        if arc.source_id in active and arc.target_id in active:
            adjacency[arc.source_id].append(arc.target_id)

    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            start = stack.index(node)
            raise GraphCycleError(stack[start:] + [node])
        visiting.add(node)
        stack.append(node)
        for neighbor in adjacency.get(node, []):
            visit(neighbor)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for task_id in active:
        visit(task_id)


def compute_readiness(
    tasks: list[Task], pipelines: list[Pipeline], edges: list[TaskEdge]
) -> dict[str, dict[str, object]]:
    active_tasks = [task for task in tasks if task.deleted_at is None]
    by_id = {task.id: task for task in active_tasks}
    predecessors: dict[str, list[GraphArc]] = defaultdict(list)

    for arc in expanded_arcs(active_tasks, pipelines, edges):
        predecessors[arc.target_id].append(arc)

    result: dict[str, dict[str, object]] = {}
    for task in active_tasks:
        unsatisfied: list[str] = []
        predecessor_ids: list[str] = []
        for arc in predecessors.get(task.id, []):
            predecessor_ids.append(arc.source_id)
            source = by_id.get(arc.source_id)
            if source is None:
                continue
            if arc.kind == "dependency" and arc.waived:
                continue
            if source.status == "done":
                continue
            # Dropped tasks are removed from derived sequencing, but never satisfy an
            # explicit dependency unless that edge has an explicit waiver.
            unsatisfied.append(source.id)

        if task.status == "blocked":
            state = "blocked"
        elif task.status in {"in_progress", "review", "done", "dropped"} and unsatisfied:
            state = "inconsistent"
        elif unsatisfied:
            state = "waiting"
        else:
            state = "ready"
        result[task.id] = {
            "readiness": state,
            "predecessor_ids": sorted(set(predecessor_ids)),
            "unsatisfied_predecessor_ids": sorted(set(unsatisfied)),
        }
    return result
