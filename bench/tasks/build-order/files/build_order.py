"""Works out the order to build things in, for the asset pipeline."""


class CycleError(Exception):
    """The dependencies go round in a circle. .cycle lists the targets in it."""

    def __init__(self, cycle):
        super().__init__("dependency cycle: " + " -> ".join(cycle + cycle[:1]))
        self.cycle = cycle


def build_order(deps):
    """deps maps a target to the list of targets it depends on. See README.md."""
    raise NotImplementedError
