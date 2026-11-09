"""Project runs are cached by a hash of every source file plus the request."""

import pytest

import app.project_runner as runner
import app.projects as projects

PROJECT_PY = "SIZE = 10.0\n"
PART_PY = "from build123d import Box\nfrom project import SIZE\n\n\ndef block():\n    return Box(SIZE, SIZE, SIZE)\n"
PREVIEW = 'from parts.block import block\nshow(block(), name="block")'


@pytest.fixture
def project(tmp_path, monkeypatch):
    base = tmp_path / "blocks"
    (base / "parts").mkdir(parents=True)
    (base / "scenes").mkdir(parents=True)
    (base / "project.py").write_text(PROJECT_PY, encoding="utf-8")
    (base / "parts" / "block.py").write_text(PART_PY, encoding="utf-8")
    monkeypatch.setattr(projects, "ROOT", tmp_path)
    monkeypatch.setattr(runner, "ROOT", tmp_path)
    runner.clear_cache()
    calls = []
    real = runner._run_locked

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(runner, "_run_locked", counting)
    yield base, calls
    runner.clear_cache()


def run(**kw):
    return runner.run_project("blocks", "part", "block", preview_source=PREVIEW, **kw)


def test_same_inputs_hit_the_cache(project):
    _, calls = project
    first = run()
    second = run()
    assert first["ok"], first.get("error")
    assert len(calls) == 1
    assert second["content_hash"] == first["content_hash"]
    assert second["shapes"] == first["shapes"]


def test_edited_file_misses(project):
    base, calls = project
    first = run()
    (base / "project.py").write_text("SIZE = 12.0\n", encoding="utf-8")
    second = run()
    assert len(calls) == 2
    assert second["content_hash"] != first["content_hash"]
    assert second["bbox"] != first["bbox"]


def test_other_project_files_are_in_the_hash(project):
    base, calls = project
    first = run()
    other = base.parent / "other"
    other.mkdir()
    (other / "project.py").write_text("X = 1\n", encoding="utf-8")
    second = run()
    assert len(calls) == 2
    assert second["content_hash"] != first["content_hash"]


def test_byte_budget_evicts_oldest(project, monkeypatch):
    _, calls = project
    first = run()
    monkeypatch.setattr(runner, "_CACHE_BYTES", len(runner.json.dumps(first, default=str)) + 1)
    runner.clear_cache()
    run()
    run(overrides={"project.py": "SIZE = 5.0\n"})
    run()
    assert len(calls) == 4


def test_overrides_and_lean_key_separately(project):
    _, calls = project
    run()
    run(overrides={"project.py": "SIZE = 5.0\n"})
    run(lean=True)
    run(overrides={"project.py": "SIZE = 5.0\n"})
    assert len(calls) == 3
