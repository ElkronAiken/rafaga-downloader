"""Ráfaga — descargas rápidas de modelos para ComfyUI.

Registra las rutas HTTP (/rafaga/*) y la extensión de la interfaz (carpeta web/).
No agrega nodos al lienzo.
"""

from .rafaga import server as _rafaga_server  # noqa: F401  (registra las rutas)

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS: dict = {}
NODE_DISPLAY_NAME_MAPPINGS: dict = {}

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
