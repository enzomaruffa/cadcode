"""The server-side PydanticAI agent — a peer editor of the one buffer (plan §4)."""

from app.agent.cad_agent import CadDeps, Patch, build_agent

__all__ = ["CadDeps", "Patch", "build_agent"]
