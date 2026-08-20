"""Fachada compatible para el nombre temporal Evidence Graph Lab.

La identidad y la implementación canónicas viven en :mod:`red_privada`. Este paquete y el
comando ``evidence-graph`` permanecen disponibles durante la serie 0.x para no romper
integraciones creadas durante el cambio de nombre.
"""

from red_privada import __version__

__all__ = ["__version__"]
