"""办公工具核心逻辑。"""

from .rmb import AmountError, to_rmb_upper

__all__ = [
    "to_rmb_upper",
    "AmountError",
]
