"""Domain verticals: thin semantic layers over the neutral kernel.

A vertical declares a domain's vocabulary and compiles it to kernel
objects; it computes no domain science and adds no kernel concepts.
Chemistry is the first vertical, not the definition (three-year
strategy: the kernel is domain-neutral by constitution).
"""

from .chemistry import ReactionTable

__all__ = ["ReactionTable"]
