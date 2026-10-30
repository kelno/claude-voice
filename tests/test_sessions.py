import pytest
from fakes import FakeClaude

from claude_voice.sessions import SessionManager
from claude_voice.store import Store


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "src"
    (r / "app").mkdir(parents=True)
    (r / "lib").mkdir()
    return r


@pytest.fixture
def manager(tmp_path, root):
    return SessionManager(Store(tmp_path / "bridge.db"), project_root=root, client_factory=FakeClaude())


def test_projects_must_be_existing_directories_under_the_root(manager, root, tmp_path):
    assert manager.resolve_project(str(root / "app")) == root / "app"
    with pytest.raises(ValueError, match="under"):
        manager.resolve_project(str(tmp_path))
    with pytest.raises(ValueError, match="under"):
        manager.resolve_project(str(root / "app" / ".." / ".."))
    with pytest.raises(ValueError, match="not a directory"):
        manager.resolve_project(str(root / "missing"))


def test_relative_project_names_resolve_against_the_root(manager, root):
    assert manager.resolve_project("app") == root / "app"
