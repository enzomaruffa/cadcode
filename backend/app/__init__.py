"""AI-native code-first CAD backend.

The one invariant: the model is a build123d Python script — the only source of
truth. Everything (geometry, history, selection, the agent's worldview) is
derived from or expressed in that script.
"""

__version__ = "0.1.0"
