import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fsrs import Card

from vocabulario.dictionary import Dictionary, normalize
from vocabulario.schedule import Schedule
from vocabulario.store import Store, maintenance_due, maintenance_label

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
    assert datetime.fromisoformat(progress["due"]) == NOW + timedelta(days=11)
    assert progress["maintenance_step"] == 0
    assert progress["learned_at"] == (NOW + timedelta(days=4)).isoformat()


def test_rapid_repeated_answers_do_not_unlock_mastery(store):
    for hour in range(6):
        now = NOW + timedelta(hours=hour)
        challenge = store.challenge(1, "casa", now)
        result = store.answer(1, challenge["id"], "casa", WORD, now)
        assert result["count"] == 1
    assert not store.mark_learned(1, "casa", NOW + timedelta(hours=6))


def test_morning_and_evening_reach_five_after_three_days(store):
    store.ensure_schedule(1, Schedule())
    morning = datetime(2026, 10, 12, 6, tzinfo=UTC)
    for day, hour in ((0, 0), (0, 11), (1, 0), (1, 11), (2, 0)):
        now = morning + timedelta(days=day, hours=hour)
        challenge = store.challenge(1, "casa", now)
        result = store.answer(1, challenge["id"], "casa", WORD, now)
        assert result["credited"]
        if day < 2:
            assert not store.mark_learned(1, "casa", now)
    assert store.progress(1, "casa")["writing_count"] == 5
    assert store.mark_learned(1, "casa", morning + timedelta(days=2))


def test_daily_limit_and_eight_hour_gap_use_local_day(store):
    store.ensure_schedule(1, Schedule())
    first = datetime(2026, 10, 12, 6, tzinfo=UTC)
    attempts = (
        (first, True, 1),
        (first + timedelta(hours=7), False, 1),
        (first + timedelta(hours=8), True, 2),
        (first + timedelta(hours=11), False, 2),
        (first + timedelta(hours=14), False, 2),
        (first + timedelta(hours=16), True, 3),
    )
    for now, credited, count in attempts:
        challenge = store.challenge(1, "casa", now)
        result = store.answer(1, challenge["id"], "casa", WORD, now)
        assert (result["credited"], result["count"]) == (credited, count)
        if now == first + timedelta(hours=7):
            assert result["next_credit"] == first + timedelta(hours=8)
        if now == first + timedelta(hours=14):
            assert result["next_credit"] == first + timedelta(hours=16)


def test_existing_answers_gain_credit_once_on_upgrade(tmp_path):
    path = tmp_path / "progress.sqlite3"
    old = Store(path)
    old.ensure_schedule(1, Schedule())
    old.add(1, "casa", NOW)
    for day, hour in ((0, 0), (0, 11), (1, 0), (1, 11), (2, 0)):
        now = NOW + timedelta(days=day, hours=hour)
        challenge = old.challenge(1, "casa", now)
        old.answer(1, challenge["id"], "casa", WORD, now)
    with old.db:
        old.db.execute("UPDATE reviews SET credited=0 WHERE reviewed_at>?", (NOW.isoformat(),))
        old.db.execute("UPDATE words SET writing_count=1,last_credit=?", (NOW.isoformat(),))
        old.db.execute("PRAGMA user_version=0")
    old.close()
    upgraded = Store(path)
    assert upgraded.progress(1, "casa")["writing_count"] == 5
    assert upgraded.db.execute("SELECT SUM(credited) FROM reviews").fetchone()[0] == 5
    upgraded.close()
    reopened = Store(path)
    assert reopened.progress(1, "casa")["writing_count"] == 5
    reopened.close()


def test_marking_mastery_twice_does_not_postpone_review(store):
    for day in range(5):
        review(store, day)
    assert store.mark_learned(1, "casa", NOW + timedelta(days=4))
    original_due = store.progress(1, "casa")["due"]
    assert store.mark_learned(1, "casa", NOW + timedelta(days=5))
    assert store.progress(1, "casa")["due"] == original_due


def test_learned_word_moves_through_weekly_biweekly_and_monthly_checks(store):
    for day in range(5):
        review(store, day)
    marked = NOW + timedelta(days=4)
    assert store.mark_learned(1, "casa", marked)
    offsets = (7, 14, 21, 28, 42, 56, 70, 84)
    for step, days in enumerate(offsets, start=1):
        due = marked + timedelta(days=days)
        assert datetime.fromisoformat(store.progress(1, "casa")["due"]) == due
        assert store.due(1, due - timedelta(seconds=1)) == []
        assert store.due(1, due)[0]["word_id"] == "casa"
        challenge = store.challenge(1, "casa", due)
        result = store.answer(1, challenge["id"], "casa", WORD, due)
        assert result["learned"]
        assert result["maintenance_step"] == step
        expected_next = (
            marked + timedelta(days=offsets[step])
            if step < len(offsets)
            else maintenance_due(due, 8, UTC)
        )
        assert result["due"] == expected_next
        assert store.mark_learned(1, "casa", expected_next)
        assert datetime.fromisoformat(store.progress(1, "casa")["due"]) == expected_next
    monthly = datetime(2027, 2, 7, 8, tzinfo=UTC)
    assert datetime.fromisoformat(store.progress(1, "casa")["due"]) == monthly
    challenge = store.challenge(1, "casa", monthly)
    assert store.answer(1, challenge["id"], "casa", WORD, monthly)["due"] == datetime(
        2027, 3, 7, 8, tzinfo=UTC
    )
    assert maintenance_label(0) == maintenance_label(3) == "раз в неделю"
    assert maintenance_label(4) == maintenance_label(7) == "раз в 2 недели"
    assert maintenance_label(8) == "раз в месяц"


def test_monthly_check_uses_calendar_month():
    assert maintenance_due(datetime(2027, 1, 31, 9, tzinfo=UTC), 8, UTC) == datetime(
        2027, 2, 28, 9, tzinfo=UTC
    )


def test_return_learned_word_to_frequent_reviews_starts_new_writing_cycle(store):
    store.ensure_schedule(1, Schedule())
    for day in range(5):
        review(store, day)
    marked = NOW + timedelta(days=4)
    assert store.mark_learned(1, "casa", marked)
    due = store.return_to_learning(1, "casa", marked + timedelta(days=1))
    progress = store.progress(1, "casa")
    assert due == datetime.fromisoformat(progress["due"])
    assert due > marked + timedelta(days=1)
    assert not progress["learned"] and progress["writing_count"] == 0
    assert progress["maintenance_step"] == 0 and progress["learned_at"] is None
    assert store.return_to_learning(1, "casa", marked + timedelta(days=1)) is None
    assert not store.mark_learned(1, "casa", marked + timedelta(days=1))
    assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 5


def test_existing_learned_word_gets_first_weekly_check_once(tmp_path):
    path = tmp_path / "progress.sqlite3"
    old = Store(path)
    old.add(1, "casa", NOW)
    for day in range(5):
        review(old, day)
    marked = NOW + timedelta(days=4, seconds=2)
    assert old.mark_learned(1, "casa", marked)
    card = old.progress(1, "casa")["card"]
    old_card = Card.from_json(card)
    old_card.due = marked + timedelta(days=30)
    with old.db:
        old.db.execute(
            "UPDATE words SET due=?,card=?,learned_at=NULL WHERE user_id=1 AND word_id='casa'",
            (old_card.due.isoformat(), old_card.to_json()),
        )
        old.db.execute("PRAGMA user_version=1")
    old.close()
    upgraded = Store(path)
    progress = upgraded.progress(1, "casa")
    assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == 2
    assert progress["learned"] and progress["writing_count"] == 5
    assert progress["maintenance_step"] == 0
    assert datetime.fromisoformat(progress["due"]) == marked + timedelta(days=7)
    upgraded.close()
    reopened = Store(path)
    assert reopened.progress(1, "casa")["due"] == progress["due"]
    reopened.close()


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
    result = review(store, 11, "caso")
    assert not result["correct"]
    assert not result["learned"]
    assert result["due"] == NOW + timedelta(days=11, minutes=10)
    assert result["count"] == 0
    assert store.progress(1, "casa")["maintenance_step"] == 0
    assert not store.mark_learned(1, "casa", NOW + timedelta(days=11))


def test_hint_on_learned_check_also_returns_to_frequent_reviews(store):
    for day in range(5):
        review(store, day)
    assert store.mark_learned(1, "casa", NOW + timedelta(days=4))
    result = review(store, 11, hinted=True)
    assert result["correct"] and result["hinted"]
    assert not result["learned"] and result["count"] == 0
    assert result["due"] == NOW + timedelta(days=11, minutes=10)
    assert store.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0] == 6


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
