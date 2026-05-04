# tests/test_session_persistence.py
from src.agents.persistence import SQLiteSessionStore
from src.agents.state import StructuringSession


def test_sqlite_store_roundtrip(tmp_path):
    db = tmp_path / "sessions.db"
    store = SQLiteSessionStore(db_path=str(db))
    session = StructuringSession(intake_nl="Buy a 1y SPY KO put")
    store.add(session)
    rehydrated = store.get(session.session_id)
    assert rehydrated is not None
    assert rehydrated.session_id == session.session_id
    assert rehydrated.intake_nl == session.intake_nl


def test_sqlite_store_survives_reopen(tmp_path):
    db = tmp_path / "sessions.db"
    store_a = SQLiteSessionStore(db_path=str(db))
    s = StructuringSession(intake_nl="hello")
    store_a.add(s)
    store_b = SQLiteSessionStore(db_path=str(db))
    assert store_b.get(s.session_id) is not None
