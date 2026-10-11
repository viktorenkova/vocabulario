import json
import sqlite3
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.test_bot import config
from vocabulario.bot import TIME_PROMPTS, Bot
from vocabulario.schedule import Schedule, due_slot
from vocabulario.store import Store

MORNING = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)  # Monday, 09:00 Moscow
EVENING = datetime(2026, 10, 12, 17, 0, tzinfo=UTC)


def test_both_sessions_and_no_duplicate_or_restart_burst():
    schedule = Schedule()
    date = "2026-10-12"
    assert due_slot(schedule, MORNING, {}) == "morning"
    assert due_slot(schedule, MORNING + timedelta(minutes=1), {"morning": date}) is None
    assert due_slot(schedule, EVENING, {"morning": date}) == "evening"
    assert due_slot(schedule, EVENING + timedelta(minutes=1), {"morning": date, "evening": date}) is None
    assert due_slot(schedule, EVENING + timedelta(hours=1), {}) == "evening"


def test_chosen_days_and_disabled_sessions():
    schedule = Schedule(weekdays=tuple(range(5)), morning_enabled=False)
    assert due_slot(schedule, MORNING, {}) is None
    assert due_slot(schedule, EVENING, {}) == "evening"
    assert due_slot(schedule, EVENING - timedelta(days=1), {}) is None  # Sunday
    assert due_slot(Schedule(weekdays=()), EVENING, {}) is None
    assert due_slot(Schedule(morning_enabled=False, evening_enabled=False), EVENING, {}) is None


def test_next_session_skips_weekends_and_respects_local_timezone():
    schedule = Schedule(weekdays=tuple(range(5)))
    friday_evening = EVENING + timedelta(days=4)
    assert schedule.next_session(MORNING) == EVENING
    assert schedule.next_session(friday_evening) == MORNING + timedelta(days=7)
    assert Schedule(weekdays=()).next_session(MORNING) is None


@pytest.mark.parametrize("bad", ["25:00", "12:60", "8:30", "hello", "09:00:30"])
def test_invalid_time_is_rejected_atomically(tmp_path, bad):
    store = Store(tmp_path / "progress.sqlite3")
    store.ensure_schedule(1, Schedule())
    with pytest.raises(ValueError):
        store.update_schedule(1, morning=bad)
    assert store.schedule(1).morning == "09:00"
    store.close()


def test_equal_enabled_times_rejected_and_disabled_time_remembered(tmp_path):
    store = Store(tmp_path / "progress.sqlite3")
    store.ensure_schedule(1, Schedule())
    with pytest.raises(ValueError):
        store.update_schedule(1, evening="09:00")
    store.update_schedule(1, evening="21:30", evening_enabled=False)
    store.update_schedule(1, evening_enabled=True)
    assert store.schedule(1).evening == "21:30"
    store.close()


def test_migration_keeps_progress_pause_and_previous_morning_delivery(tmp_path):
    path = tmp_path / "old.sqlite3"
    db = sqlite3.connect(path)
    db.execute(
        "CREATE TABLE preferences(user_id INTEGER PRIMARY KEY,chat_id INTEGER NOT NULL,"
        "enabled INTEGER NOT NULL DEFAULT 1,last_reminder TEXT)"
    )
    db.execute("INSERT INTO preferences VALUES(1,1,0,'2026-10-12')")
    db.commit()
    db.close()
    store = Store(path)
    store.add(1, "casa", MORNING)
    store.ensure_schedule(1, Schedule())
    assert store.preference(1)["enabled"] == 0
    assert store.stats(1, MORNING)["total"] == 1
    assert store.delivered(1, "2026-10-12") == {"morning": "2026-10-12"}
    assert due_slot(store.schedule(1), EVENING, store.delivered(1, "2026-10-12")) == "evening"
    store.close()


def test_settings_and_deliveries_survive_restart(tmp_path):
    path = tmp_path / "progress.sqlite3"
    first = Store(path)
    first.ensure_schedule(1, Schedule())
    first.update_schedule(1, morning="08:30", evening="21:15", weekdays=(0, 2, 4))
    first.mark_delivered(1, "2026-10-12", "morning")
    first.close()
    second = Store(path)
    second.ensure_schedule(1, Schedule())
    assert second.schedule(1) == Schedule("08:30", "21:15", (0, 2, 4))
    assert second.delivered(1, "2026-10-12") == {"morning": "2026-10-12"}
    assert json.loads(second.export(1))["schedule"]["weekdays"] == [0, 2, 4]
    second.close()


def test_active_words_return_in_evening_without_double_writing_credit(tmp_path):
    store = Store(tmp_path / "progress.sqlite3")
    store.ensure_schedule(1, Schedule())
    word = {"id": "casa", "word": "casa"}
    store.add(1, "casa", MORNING)
    challenge = store.challenge(1, "casa", MORNING)
    morning = store.answer(1, challenge["id"], "casa", word, MORNING)
    assert morning["due"] == EVENING
    challenge = store.challenge(1, "casa", EVENING)
    evening = store.answer(1, challenge["id"], "casa", word, EVENING)
    assert evening["count"] == 1
    assert evening["due"] == MORNING + timedelta(days=1)
    store.close()


async def test_edit_time_never_grades_open_review_and_rejects_bad_input(tmp_path):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"])
    pending = bot.store.challenge(1, word["id"])
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    context = SimpleNamespace(user_data={})
    await bot.begin_time_edit(chat, context, "morning")
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        effective_message=SimpleNamespace(text="25:00"),
    )
    await bot.text(update, context)
    assert context.user_data["editing_time"] == "morning"
    assert bot.store.schedule(1).morning == "09:00"
    update.effective_message.text = "08:30"
    await bot.text(update, context)
    assert bot.store.schedule(1).morning == "08:30"
    assert bot.store.pending(1)["id"] == pending["id"]
    assert bot.store.progress(1, word["id"])["writing_count"] == 0
    bot.store.close()


async def test_reply_to_time_prompt_survives_restart(tmp_path):
    bot = Bot(config(tmp_path))
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        effective_message=SimpleNamespace(
            text="21:30",
            reply_to_message=SimpleNamespace(
                text=TIME_PROMPTS["evening"], from_user=SimpleNamespace(is_bot=True)
            ),
        ),
    )
    await bot.text(update, SimpleNamespace(user_data={}))
    assert bot.store.schedule(1).evening == "21:30"
    bot.store.close()


async def test_real_reminder_path_twice_a_day_then_paused(tmp_path, monkeypatch):
    import vocabulario.bot as module

    now = MORNING
    monkeypatch.setattr(module, "utcnow", lambda: now)
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"], MORNING)
    bot.store.subscribe(1, 1)
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_chat=AsyncMock(return_value=chat)))
    await bot.reminder(context)
    assert chat.send_message.await_count == 2
    await bot.reminder(context)
    assert chat.send_message.await_count == 2
    now = EVENING
    await bot.reminder(context)
    assert chat.send_message.await_count == 4
    assert set(bot.store.delivered(1, "2026-10-12")) == {"morning", "evening"}
    bot.store.pause(1)
    now = MORNING + timedelta(days=1)
    await bot.reminder(context)
    assert chat.send_message.await_count == 4
    bot.store.close()


async def test_settings_buttons_change_days_sessions_and_pause(tmp_path):
    bot = Bot(config(tmp_path))
    bot.store.subscribe(1, 1)
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    query = SimpleNamespace(data="sched_day:6", answer=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_chat=chat, callback_query=query)
    context = SimpleNamespace(user_data={})
    await bot.callback(update, context)
    assert bot.store.schedule(1).weekdays == tuple(range(6))
    query.data = "sched_days:weekdays"
    await bot.callback(update, context)
    assert bot.store.schedule(1).weekdays == tuple(range(5))
    query.data = "sched_toggle:evening"
    await bot.callback(update, context)
    assert not bot.store.schedule(1).evening_enabled
    query.data = "sched_pause"
    await bot.callback(update, context)
    assert not bot.store.preference(1)["enabled"]
    await bot.callback(update, context)
    assert bot.store.preference(1)["enabled"]
    assert not bot.store.schedule(1).evening_enabled
    query.data = "sched_time:morning"
    await bot.callback(update, context)
    assert context.user_data["editing_time"] == "morning"
    bot.store.close()
