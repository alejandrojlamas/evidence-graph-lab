"""Public package facade for Evidence Graph Lab.

The implementation remains in :mod:`red_privada` for compatibility with the initial 0.1
release. New integrations should import :mod:`evidence_graph_lab` and use the
``evidence-graph`` command.
"""

from red_privada import __version__

__all__ = ["__version__"]
