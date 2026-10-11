import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from vocabulario.dictionary import Dictionary, normalize
from vocabulario.store import Store

NOW = datetime(2026, 10, 11, 8, tzinfo=UTC)
WORD = {"id": "casa", "word": "casa"}


@pytest.fixture
def store(tmp_path):
    s = Store(tmp_path / "progress.sqlite3")
    s.add(1, WORD["id"], NOW)
    yield s
    s.close()


def review(store, day=0, answer="casa", hinted=False, word=WORD):
    now = NOW + timedelta(days=day)
    challenge = store.challenge(1, word["id"], now)
    if hinted:
        store.hint(1, challenge["id"])
    return store.answer(1, challenge["id"], answer, word, now=now)


def test_five_spaced_written_answers_required(store):
    for day in range(4):
        assert review(store, day)["credited"]
        assert not store.mark_learned(1, "casa", NOW + timedelta(days=day))
    assert review(store, 4)["count"] == 5
    assert store.mark_learned(1, "casa", NOW + timedelta(days=4))
    progress = store.progress(1, "casa")
    assert progress["learned"]
    assert datetime.fromisoformat(progress["due"]) >= NOW + timedelta(days=34)


def test_rapid_repeated_answers_do_not_unlock_mastery(store):
    for hour in range(6):
        now = NOW + timedelta(hours=hour)
        challenge = store.challenge(1, "casa", now)
        result = store.answer(1, challenge["id"], "casa", WORD, now)
        assert result["count"] == 1
    assert not store.mark_learned(1, "casa", NOW + timedelta(hours=6))


def test_marking_mastery_twice_does_not_postpone_review(store):
    for day in range(5):
        review(store, day)
    assert store.mark_learned(1, "casa", NOW + timedelta(days=4))
    original_due = store.progress(1, "casa")["due"]
    assert store.mark_learned(1, "casa", NOW + timedelta(days=5))
    assert store.progress(1, "casa")["due"] == original_due


def test_hint_and_dictionary_reveal_do_not_count(store):
    assert review(store, hinted=True)["count"] == 0
    challenge = store.challenge(1, "casa", NOW + timedelta(days=1))
    store.reveal_word(1, "casa")
    result = store.answer(1, challenge["id"], "casa", WORD, NOW + timedelta(days=1))
    assert result["count"] == 0
    assert result["hinted"]


def test_duplicate_answer_cannot_count_twice(store):
    challenge = store.challenge(1, "casa", NOW)
    assert store.answer(1, challenge["id"], "casa", WORD, NOW)
    assert store.answer(1, challenge["id"], "casa", WORD, NOW) is None
    assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 1


def test_wrong_user_and_wrong_word_cannot_grade_challenge(store):
    challenge = store.challenge(1, "casa", NOW)
    assert store.answer(2, challenge["id"], "casa", WORD, NOW) is None
    assert store.answer(1, challenge["id"], "perro", {"id": "perro", "word": "perro"}, NOW) is None
    assert store.pending(1)


def test_error_after_mastery_returns_to_active_learning(store):
    for day in range(5):
        review(store, day)
    assert store.mark_learned(1, "casa", NOW + timedelta(days=4))
    result = review(store, 34, "caso")
    assert not result["correct"]
    assert not result["learned"]
    assert result["due"] == NOW + timedelta(days=34, minutes=10)
    assert not store.mark_learned(1, "casa", NOW + timedelta(days=34))


@pytest.mark.parametrize(("target", "answer"), [("año", "ano"), ("sí", "si"), ("papá", "papa")])
def test_diacritics_are_required(store, target, answer):
    word = {"id": target, "word": target}
    store.add(1, target, NOW)
    result = review(store, answer=answer, word=word)
    assert not result["correct"]
    assert result["count"] == 0


def test_case_whitespace_and_unicode_are_normalized():
    assert normalize("  ÁRBOL  ") == normalize("a\u0301rbol")
    assert normalize("darse   cuenta") == "darse cuenta"


def test_pending_and_progress_survive_restart(tmp_path):
    path = tmp_path / "progress.sqlite3"
    first = Store(path)
    first.add(1, "casa", NOW)
    challenge = first.challenge(1, "casa", NOW)
    first.hint(1, challenge["id"])
    first.close()
    second = Store(path)
    assert second.pending(1)["id"] == challenge["id"]
    assert second.pending(1)["hinted"]
    assert second.answer(1, challenge["id"], "casa", WORD, NOW)["count"] == 0
    second.close()


def test_late_review_still_due_and_selection_is_idempotent(store):
    assert store.due(1, NOW + timedelta(days=50))[0]["word_id"] == "casa"
    assert not store.add(1, "casa", NOW)
    assert store.stats(1, NOW)["total"] == 1


def test_export_contains_history_without_credentials(store):
    review(store)
    exported = json.loads(store.export(1))
    assert len(exported["words"]) == len(exported["reviews"]) == 1
    assert "token" not in exported


def test_bundled_dictionary_has_required_fields():
    dictionary = Dictionary(Path("data/dictionary.jsonl"))
    assert len(dictionary.words) >= 5000
    assert len(dictionary.topics()) >= 15
    for word in dictionary.ordered:
        assert word["ipa"] and word["meanings"] and word["topics"]
        assert len(word["examples"]) >= 2
        assert all(x["source"] and x["attribution"] for x in word["examples"])
        if word["pos"] == "v":
            assert "Indicativo · Presente" in word["conjugations"]


def test_search_by_spanish_english_and_topic():
    dictionary = Dictionary(Path("data/dictionary.jsonl"))
    found, _ = dictionary.search("arbol")
    assert found[0]["word"] == "árbol"
    found, _ = dictionary.search("apple", topic="Еда и напитки")
    assert any(w["word"] == "manzana" for w in found)
