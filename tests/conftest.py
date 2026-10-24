"""Shared fixtures. `book_env` points the app at the fixture books and a throwaway data dir."""

import shutil
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "books"


@pytest.fixture
def book_env(tmp_path, monkeypatch):
    """Return the books root. DB and data dir live in tmp_path; BOOKS_DIR is the fixture books.

    app.db binds DATA_DIR and DB_PATH at import, so those are patched there too.
    """
    from app import config, db

    data = tmp_path / "data"
    monkeypatch.setenv("THEMATHS_BOOKS", str(FIXTURES))
    monkeypatch.setenv("THEMATHS_DATA", str(data))
    monkeypatch.setattr(config, "BOOKS_DIR", FIXTURES)
    monkeypatch.setattr(config, "DATA_DIR", data)
    monkeypatch.setattr(config, "DB_PATH", data / "themaths.db")
    monkeypatch.setattr(db, "DATA_DIR", data)
    monkeypatch.setattr(db, "DB_PATH", data / "themaths.db")
    return FIXTURES


@pytest.fixture
def copy_book(book_env, tmp_path, monkeypatch):
    """Copy the demo book into tmp_path, point BOOKS_DIR there, return the books root to edit."""
    from app import config

    root = tmp_path / "books"
    shutil.copytree(FIXTURES, root)
    monkeypatch.setattr(config, "BOOKS_DIR", root)
    return root
