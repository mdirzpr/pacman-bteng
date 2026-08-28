"""Live introspection of the running behavior trees.

BTEng's ``Inspector`` sees every node transition; this package fans that out to
a console log, a JSON-lines file and a ZMQ publisher that the two viewers in
``tools/`` subscribe to.  There is no Groot2 here — Groot2 speaks
BehaviorTree.CPP's wire protocol, which BTEng's event stream is not.
"""

from pacman_bteng.introspection.stream import BTIntrospection

__all__ = ["BTIntrospection"]
