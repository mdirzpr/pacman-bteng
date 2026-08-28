"""The BTEng layer: node library plus the runners that keep the trees ticking.

Importing this package is what registers every Pac-Man node with BTEng's global
``NodeFactory`` — the XML parser resolves ``<SetChaseTarget/>`` by looking the
tag up there, so the import has to happen before any tree is parsed.
:class:`AgentBrain` does that for you by importing the node modules itself.
"""

from pacman_bteng.behaviors import actions, conditions  # noqa: F401 - registration
from pacman_bteng.behaviors import blackboard_keys
from pacman_bteng.behaviors.nodes import (
    GameCondition,
    GameNode,
    StepAction,
    TargetSelector,
)
from pacman_bteng.behaviors.runner import (
    AgentBrain,
    BrainError,
    BrainPool,
    node_catalogue,
    registered_nodes,
)

__all__ = [
    "actions",
    "conditions",
    "blackboard_keys",
    "GameNode",
    "GameCondition",
    "TargetSelector",
    "StepAction",
    "AgentBrain",
    "BrainPool",
    "BrainError",
    "node_catalogue",
    "registered_nodes",
]
