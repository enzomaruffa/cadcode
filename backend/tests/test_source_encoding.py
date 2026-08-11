"""Project source must decode as UTF-8 no matter what the ambient locale says.

Prod ran with a C/POSIX locale, so every bare ``read_text()`` resolved to ASCII
and any em-dash in a project docstring turned into a 500 — /projects/<p>/run,
/projects/<p>/file and /completions all failed together. These tests force that
locale back on and pin the source-reading paths against it.
"""

import io
import locale

import pytest

import app.completions as completions
import app.projects as projects
from app.project_runner import _read

PROJECT_PY = '"""Constants — imported by every part."""\n\nWALL = 2.0\nUNIT = 10.0\n'
PART_PY = (
    '"""A part module — em-dash in the module docstring."""\n\n\n'
    'def widget():\n    """A widget — em-dash in the function docstring."""\n    return None\n'
)


@pytest.fixture
def ascii_locale(monkeypatch):
    """Make "no encoding given" mean ASCII, exactly as it did on prod.

    pathlib resolves a missing encoding through io.text_encoding() before it ever
    reaches io.open, so that is the seam. Explicit encodings pass through
    untouched — which is the whole point of the fix.
    """
    monkeypatch.setattr(io, "text_encoding", lambda enc=None, stacklevel=2: enc if enc is not None else "ascii")
    monkeypatch.setattr(locale, "getencoding", lambda: "ascii")


@pytest.fixture
def project(tmp_path, monkeypatch):
    base = tmp_path / "widgets"
    (base / "parts").mkdir(parents=True)
    (base / "scenes").mkdir(parents=True)
    (base / "project.py").write_text(PROJECT_PY, encoding="utf-8")
    (base / "parts" / "widget.py").write_text(PART_PY, encoding="utf-8")
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    monkeypatch.setattr(completions, "ROOT", tmp_path)
    return base


def test_repro_bare_read_fails_under_ascii(ascii_locale, project):
    """Guard the guard: if this stops raising, the fixture no longer reproduces."""
    with pytest.raises(UnicodeDecodeError):
        (project / "project.py").read_text()


def test_project_runner_read(ascii_locale, project):
    assert "—" in _read(project / "project.py")


def test_projects_read_file(ascii_locale, project):
    assert "—" in projects.read_file("widgets", "part", "widget")["source"]


def test_projects_round_trip_write(ascii_locale, project):
    projects.write_file("widgets", "part", "widget", PART_PY)
    assert "—" in projects.read_file("widgets", "part", "widget")["source"]


def test_completions(ascii_locale, project):
    assert "WALL" in completions._constants("widgets")
    assert "—" in completions._part_info("widgets", "widget")["doc"]
