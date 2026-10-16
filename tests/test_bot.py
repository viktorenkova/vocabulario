from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest
from telegram.error import BadRequest, NetworkError, TimedOut
from telegram.ext import Application

from vocabulario.bot import SEARCH_LABEL, SEARCH_PROMPT, Bot, card_text
from vocabulario.config import Config
from vocabulario.schedule import Schedule, due_slot


def config(tmp_path):
    return Config(
        "123456:fake_token_for_offline_tests",
        1,
        ZoneInfo("Europe/Moscow"),
        time(9, 0),
        15,
        0.9,
        "es-ES-ElviraNeural",
        Path("data/dictionary.jsonl"),
        tmp_path / "db.sqlite3",
    )


def test_application_builds_without_network(tmp_path):
    bot = Bot(config(tmp_path))
    app = bot.application()
    assert isinstance(app, Application)
    assert len(app.job_queue.jobs()) == 1
    bot.store.close()


def test_reminder_respects_timezone_pause_and_last_sent(tmp_path):
    schedule = Schedule()
    assert due_slot(schedule, datetime(2026, 10, 11, 5, 59, tzinfo=UTC), {}) is None
    assert due_slot(schedule, datetime(2026, 10, 11, 6, 0, tzinfo=UTC), {}) == "morning"
    sent = {"morning": "2026-10-11"}
    assert due_slot(schedule, datetime(2026, 10, 11, 16, 59, tzinfo=UTC), sent) is None
    assert due_slot(schedule, datetime(2026, 10, 11, 17, 0, tzinfo=UTC), sent) == "evening"


async def test_prompt_hides_answer_and_text_grades_it(tmp_path):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"])
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    context = SimpleNamespace(user_data={})
    await bot.prompt(chat, 1, context)
    text = chat.send_message.call_args.args[0]
    assert "house" in text and "casa" not in text
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        effective_message=SimpleNamespace(text="CASA"),
    )
    await bot.text(update, context)
    assert bot.store.progress(1, word["id"])["writing_count"] == 1
    assert bot.store.pending(1) is None
    bot.store.close()


async def test_writing_feedback_shows_count_and_next_eligible_time(tmp_path, monkeypatch):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    morning = datetime(2026, 10, 12, 6, tzinfo=UTC)
    bot.store.add(1, word["id"], morning)
    first = bot.store.challenge(1, word["id"], morning)
    assert bot.store.answer(1, first["id"], "casa", word, morning)["count"] == 1
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    await bot.show_word(chat, 1, word["id"])
    assert "Зачтено написаний: 1/5" in chat.send_message.call_args.args[0]
    assert "Отметить выученным" not in str(chat.send_message.call_args.kwargs["reply_markup"])
    early = morning + timedelta(hours=2)
    second = bot.store.challenge(1, word["id"], early)
    monkeypatch.setattr("vocabulario.store.utcnow", lambda: early)
    await bot.finish(chat, 1, second, "casa", SimpleNamespace(user_data={}))
    feedback = chat.send_message.call_args.args[0]
    assert "Зачтено написаний: 1/5" in feedback
    assert "Следующий зачёт возможен с 12.10 17:00" in feedback
    assert "24 часа" not in feedback
    bot.store.close()


async def test_learned_card_check_and_return_button(tmp_path, monkeypatch):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    base = datetime(2026, 10, 11, 6, tzinfo=UTC)
    bot.store.add(1, word["id"], base)
    for day in range(5):
        now = base + timedelta(days=day)
        challenge = bot.store.challenge(1, word["id"], now)
        bot.store.answer(1, challenge["id"], "casa", word, now)
    marked = base + timedelta(days=4)
    assert bot.store.mark_learned(1, word["id"], marked)
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    await bot.show_word(chat, 1, word["id"])
    assert "Проверка раз в неделю" in chat.send_message.call_args.args[0]
    assert "Учить заново" in str(chat.send_message.call_args.kwargs["reply_markup"])

    foreign_chat = SimpleNamespace(id=2, type="private", send_message=AsyncMock())
    foreign_query = SimpleNamespace(data=f"relearn:{word['id']}", answer=AsyncMock())
    foreign = SimpleNamespace(
        effective_user=SimpleNamespace(id=2), effective_chat=foreign_chat, callback_query=foreign_query
    )
    await bot.callback(foreign, SimpleNamespace(user_data={}))
    assert bot.store.progress(1, word["id"])["learned"]
    foreign_chat.send_message.assert_not_awaited()

    due = marked + timedelta(days=7)
    bot.store.challenge(1, word["id"], due)
    await bot.prompt(chat, 1, SimpleNamespace(user_data={}), now=due)
    assert "Проверка выученного слова" in chat.send_message.call_args.args[0]
    monkeypatch.setattr("vocabulario.store.utcnow", lambda: due)
    await bot.finish(chat, 1, bot.store.pending(1), "casa", SimpleNamespace(user_data={}))
    assert "Слово остаётся выученным" in chat.send_message.call_args.args[0]
    assert "Учить заново" in str(chat.send_message.call_args.kwargs["reply_markup"])

    returned_at = due + timedelta(days=1)
    monkeypatch.setattr("vocabulario.store.utcnow", lambda: returned_at)
    query = SimpleNamespace(data=f"relearn:{word['id']}", answer=AsyncMock())
    update = SimpleNamespace(effective_user=SimpleNamespace(id=1), effective_chat=chat, callback_query=query)
    await bot.callback(update, SimpleNamespace(user_data={}))
    assert "Новый цикл: 0/5" in chat.send_message.call_args.args[0]
    assert not bot.store.progress(1, word["id"])["learned"]
    assert bot.store.progress(1, word["id"])["writing_count"] == 0
    bot.store.close()


async def test_foreign_user_cannot_read_or_mutate(tmp_path):
    bot = Bot(config(tmp_path))
    chat = SimpleNamespace(id=2, type="private", send_message=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=2),
        effective_chat=chat,
        effective_message=SimpleNamespace(text="casa"),
    )
    await bot.text(update, SimpleNamespace(user_data={}))
    chat.send_message.assert_not_awaited()
    assert bot.store.stats(2)["total"] == 0
    bot.store.close()


async def test_search_button_and_query_preserve_open_review(tmp_path):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"])
    pending = bot.store.challenge(1, word["id"])
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    context = SimpleNamespace(user_data={})
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        effective_message=SimpleNamespace(text=SEARCH_LABEL),
    )
    await bot.text(update, context)
    assert chat.send_message.call_args.args[0] == SEARCH_PROMPT
    update.effective_message.text = "manzana"
    await bot.text(update, context)
    assert bot.store.pending(1)["id"] == pending["id"]
    assert bot.store.progress(1, word["id"])["writing_count"] == 0
    assert "manzana" in str(chat.send_message.call_args.kwargs["reply_markup"])
    assert not context.user_data["awaiting_search"]
    bot.store.close()


async def test_search_exposing_current_answer_counts_as_hint(tmp_path):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"])
    bot.store.challenge(1, word["id"])
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    context = SimpleNamespace(user_data={"awaiting_search": True})
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        effective_message=SimpleNamespace(text="casa"),
    )
    await bot.text(update, context)
    assert bot.store.pending(1)["hinted"]
    await bot.text(update, context)
    assert bot.store.progress(1, word["id"])["writing_count"] == 0
    bot.store.close()


async def test_search_prompt_reply_is_safe_after_restart(tmp_path):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"])
    pending = bot.store.challenge(1, word["id"])
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        effective_message=SimpleNamespace(
            text="manzana",
            reply_to_message=SimpleNamespace(text=SEARCH_PROMPT, from_user=SimpleNamespace(is_bot=True)),
        ),
    )
    await bot.text(update, SimpleNamespace(user_data={}))
    assert bot.store.pending(1)["id"] == pending["id"]
    assert "manzana" in str(chat.send_message.call_args.kwargs["reply_markup"])
    bot.store.close()


async def test_search_command_without_query_asks_for_word(tmp_path):
    bot = Bot(config(tmp_path))
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        effective_message=SimpleNamespace(text="/search"),
    )
    context = SimpleNamespace(user_data={}, args=[])
    await bot.command(update, context)
    assert context.user_data["awaiting_search"]
    assert chat.send_message.call_args.args[0] == SEARCH_PROMPT
    bot.store.close()


def test_dictionary_cards_fit_telegram_limit(tmp_path):
    bot = Bot(config(tmp_path))
    assert max(len(card_text(w)) for w in bot.dictionary.ordered) < 4096
    bot.store.close()


@pytest.mark.parametrize("error", [TimedOut(), BadRequest("Query is too old")])
async def test_word_opens_even_if_callback_acknowledgement_fails(tmp_path, error):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "gimnasio")
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=1),
        effective_chat=chat,
        callback_query=SimpleNamespace(data=f"word:{word['id']}", answer=AsyncMock(side_effect=error)),
    )
    await bot.callback(update, SimpleNamespace(user_data={}))
    assert "<b>gimnasio</b>" in chat.send_message.call_args.args[0]
    assert bot.store.stats(1)["total"] == 0
    bot.store.close()


async def test_callback_acknowledgement_failure_does_not_bypass_owner_check(tmp_path):
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "gimnasio")
    chat = SimpleNamespace(id=2, type="private", send_message=AsyncMock())
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=2),
        effective_chat=chat,
        callback_query=SimpleNamespace(data=f"add:{word['id']}", answer=AsyncMock(side_effect=TimedOut())),
    )
    await bot.callback(update, SimpleNamespace(user_data={}))
    chat.send_message.assert_not_awaited()
    assert bot.store.stats(2)["total"] == 0
    bot.store.close()


async def test_reminder_sends_question_once_and_persists_date(tmp_path, monkeypatch):
    import vocabulario.bot as module

    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    monkeypatch.setattr(module, "utcnow", lambda: now)
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"], now)
    bot.store.subscribe(1, 1)
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock())
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_chat=AsyncMock(return_value=chat)))
    await bot.reminder(context)
    assert chat.send_message.await_count == 2
    assert bot.store.pending(1)["word_id"] == word["id"]
    await bot.reminder(context)
    assert chat.send_message.await_count == 2
    bot.store.close()


async def test_failed_reminder_can_retry(tmp_path, monkeypatch):
    import vocabulario.bot as module

    now = datetime(2026, 10, 11, 6, 0, tzinfo=UTC)
    monkeypatch.setattr(module, "utcnow", lambda: now)
    bot = Bot(config(tmp_path))
    word = next(w for w in bot.dictionary.ordered if w["word"] == "casa")
    bot.store.add(1, word["id"], now)
    bot.store.subscribe(1, 1)
    chat = SimpleNamespace(id=1, type="private", send_message=AsyncMock(side_effect=NetworkError("offline")))
    context = SimpleNamespace(user_data={}, bot=SimpleNamespace(get_chat=AsyncMock(return_value=chat)))
    await bot.reminder(context)
    preference = bot.store.db.execute("SELECT * FROM preferences WHERE user_id=1").fetchone()
    assert preference["last_reminder"] is None
    chat.send_message.side_effect = None
    await bot.reminder(context)
    assert (
        bot.store.db.execute("SELECT last_reminder FROM preferences WHERE user_id=1").fetchone()[0]
        == "2026-10-11"
    )
    bot.store.close()
