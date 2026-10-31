"""Proceso ARGON: Excel de contenedores de argón de Carburos → pedidos de venta en Odoo 18.

Los módulos se importan entre sí por su nombre (argon_lector, argon_facturacion…), igual que en los programas
de escritorio, por eso esta carpeta se añade al path.
"""
import sys
from pathlib import Path

_aqui = str(Path(__file__).parent)
if _aqui not in sys.path:
    sys.path.insert(0, _aqui)
