"""The kernel: runs the build123d source and turns it into geometry.

M0 runs the script in-process (``InProcessKernel``). M1 swaps in a sandboxed
subprocess kernel implementing the same ``Kernel`` interface — re-exec the whole
script per edit, deterministic and stateless, matching code-as-truth.
"""

from app.kernel.inprocess import InProcessKernel, Kernel
from app.kernel.result import RunResult
from app.kernel.subprocess_kernel import SubprocessKernel

__all__ = ["RunResult", "Kernel", "InProcessKernel", "SubprocessKernel"]
