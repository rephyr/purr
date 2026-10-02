import heapq


class CycleError(Exception):
    def __init__(self, cycle):
        super().__init__("dependency cycle: " + " -> ".join(cycle + cycle[:1]))
        self.cycle = cycle


def build_order(deps):
    graph = {t: list(d) for t, d in deps.items()}
    for ds in deps.values():
        for d in ds:
            graph.setdefault(d, [])
    waiting = {t: len(set(d)) for t, d in graph.items()}
    users = {t: [] for t in graph}
    for t, ds in graph.items():
        for d in set(ds):
            users[d].append(t)
    ready = [t for t, n in waiting.items() if n == 0]
    heapq.heapify(ready)
    order = []
    while ready:
        t = heapq.heappop(ready)
        order.append(t)
        for u in users[t]:
            waiting[u] -= 1
            if waiting[u] == 0:
                heapq.heappush(ready, u)
    if len(order) == len(graph):
        return order
    left = {t for t in graph if t not in order}
    start = min(left)
    path, seen = [], {}
    t = start
    while t not in seen:  # every target left has a dependency that's left too
        seen[t] = len(path)
        path.append(t)
        t = min(d for d in graph[t] if d in left)
    cycle = path[seen[t]:]
    i = cycle.index(min(cycle))
    raise CycleError(cycle[i:] + cycle[:i])

