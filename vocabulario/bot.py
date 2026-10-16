import html
import io
import logging
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from aiohttp import ClientError
from edge_tts.exceptions import EdgeTTSException
from telegram import ForceReply, KeyboardButton, ReplyKeyboardMarkup, Update
from telegram import InlineKeyboardButton as Button
from telegram import InlineKeyboardMarkup as Keyboard
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .config import Config
from .dictionary import Dictionary
from .media import Audio
from .network import exception_names, telegram_request
from .schedule import DAY_NAMES, Schedule, due_slot, validate_time
from .store import Store, maintenance_label, utcnow

SEARCH_LABEL = "🔎 Найти слово"
REVIEW_LABEL = "📚 Повторить"
MY_LABEL = "⭐ Мои слова"
SETTINGS_LABEL = "⚙️ Расписание"
SEARCH_PROMPT = "🔎 Напишите испанское слово или английское значение, например casa или apple."
TIME_PROMPTS = {
    "morning": "🕘 Введите время для утреннего занятия в формате ЧЧ:ММ, например 08:30.",
    "evening": "🕘 Введите время для вечернего занятия в формате ЧЧ:ММ, например 20:30.",
}
HOME_KEYBOARD = ReplyKeyboardMarkup(
    [
        [KeyboardButton(SEARCH_LABEL)],
        [KeyboardButton(REVIEW_LABEL), KeyboardButton(MY_LABEL)],
        [KeyboardButton(SETTINGS_LABEL)],
    ],
    resize_keyboard=True,
    is_persistent=True,
)

HELP = """🇪🇸 Vocabulario — ваш испанский словарь

Нажмите «🔎 Найти слово» и напишите интересующее слово.
/topics — слова по темам
/search — написать слово для поиска (или /search casa)
/words — весь словарь
/my — выбранные слова
/review — повторить слова, для которых наступил срок
/stats — прогресс
/settings — время и дни утреннего и вечернего занятия
/pause и /resume — выключить и включить ежедневные занятия
/cancel — закончить текущий вопрос как «не помню»
/export — скачать прогресс
/id — ваш Telegram ID

Откройте карточку → «Учить». В повторении бот показывает значение: напишите испанское слово.
Регистр не важен, но ñ и ударения обязательны: año ≠ ano, sí ≠ si.
После 5 верных написаний без подсказок можно нажать «Выучено».
Засчитываются не более 2 написаний за день, с промежутком не менее 8 часов.
Выученные слова: 4 еженедельные проверки, затем 4 проверки раз в две недели, потом раз в месяц.
Кнопка «Учить заново» возвращает слово в частые повторы. Ошибка тоже возвращает его туда.
Переводы и примеры в первой версии — на английском."""


def esc(value):
    return html.escape(str(value))


def card_text(word, progress=None):
    meanings = "\n".join("• " + esc(x[:240]) for x in word["meanings"])
    text = f"<b>{esc(word['word'])}</b> · {esc(word['pos'])}\n{esc(word['ipa'])}\n\n{meanings}"
    text += "\n\nТемы: " + esc(", ".join(word["topics"]))
    for example in word["examples"]:
        text += f"\n\n<i>{esc(example['es'])}</i>\n{esc(example['en'])}"
        text += f'\n<a href="{esc(example["source"])}">Tatoeba</a> · {esc(example["attribution"])}'
    text += f'\n\n<a href="{esc(word["source"])}">Wiktionary · CC BY-SA</a>'
    if progress:
        text += f"\nЗачтено написаний: {min(progress['writing_count'], 5)}/5 · " + (
            "выучено" if progress["learned"] else "изучается"
        )
    return text


class Bot:
    def __init__(self, config):
        self.config = config
        self.dictionary = Dictionary(config.dictionary)
        self.store = Store(config.database, config.retention)
        created = self.store.ensure_schedule(
            config.user_id,
            Schedule(
                morning=config.reminder.strftime("%H:%M"),
                evening=config.evening_reminder.strftime("%H:%M"),
                timezone=str(config.timezone),
            ),
        )
        if created:
            self.store.update_schedule(config.user_id)
        self.audio = Audio(config.voice)
        self.topic_names = list(self.dictionary.topics())

    def authorized(self, update):
        return (
            update.effective_user is not None
            and update.effective_user.id == self.config.user_id
            and update.effective_chat is not None
            and update.effective_chat.type == "private"
        )

    async def send(self, chat, text, keyboard=None):
        await chat.send_message(text, parse_mode="HTML", reply_markup=keyboard, disable_web_page_preview=True)
        logging.getLogger(__name__).info("Reply delivered")

    async def begin_search(self, chat, context):
        context.user_data["awaiting_search"] = True
        await self.send(chat, SEARCH_PROMPT, ForceReply(input_field_placeholder="Например: casa или apple"))

    async def show_settings(self, chat, user_id):
        schedule = self.store.schedule(user_id)
        preference = self.store.preference(user_id)
        enabled = bool(preference and preference["enabled"])
        days = ", ".join(DAY_NAMES[d] for d in schedule.weekdays) if schedule.weekdays else "не выбраны"
        if len(schedule.weekdays) == 7:
            days = "каждый день"
        zone = "Москва" if schedule.timezone == "Europe/Moscow" else schedule.timezone
        text = (
            f"⚙️ <b>Расписание повторений</b>\n\nВремя: {esc(zone)}\n"
            f"🌅 Утро: {schedule.morning}"
            + ("" if schedule.morning_enabled else " — выключено")
            + f"\n🌙 Вечер: {schedule.evening}"
            + ("" if schedule.evening_enabled else " — выключено")
            + f"\nДни: {esc(days)}\nНапоминания: "
            + ("включены" if enabled else "на паузе")
            + "\n\nНажмите время, чтобы изменить его. Нажмите день, чтобы включить или исключить его. "
            "Занятие приходит, если есть слова для повторения."
        )
        rows = []
        for slot, label in (("morning", "🌅 Утро"), ("evening", "🌙 Вечер")):
            active = getattr(schedule, slot + "_enabled")
            rows.append(
                [
                    Button(
                        f"{label} · {getattr(schedule, slot)} · изменить", callback_data=f"sched_time:{slot}"
                    ),
                    Button("Выключить" if active else "Включить", callback_data=f"sched_toggle:{slot}"),
                ]
            )
        days_buttons = [
            Button(("✓ " if day in schedule.weekdays else "") + name, callback_data=f"sched_day:{day}")
            for day, name in enumerate(DAY_NAMES)
        ]
        rows.extend([days_buttons[:4], days_buttons[4:]])
        rows.append(
            [
                Button("Каждый день", callback_data="sched_days:all"),
                Button("Будни", callback_data="sched_days:weekdays"),
                Button("Выходные", callback_data="sched_days:weekends"),
            ]
        )
        rows.append([Button("⏸ Пауза" if enabled else "▶️ Возобновить", callback_data="sched_pause")])
        await self.send(chat, text, Keyboard(rows))

    async def begin_time_edit(self, chat, context, slot):
        if slot not in TIME_PROMPTS:
            return
        context.user_data["editing_time"] = slot
        await self.send(chat, TIME_PROMPTS[slot], ForceReply(input_field_placeholder="ЧЧ:ММ, например 08:30"))

    async def save_time(self, chat, user_id, context, slot, value):
        try:
            self.store.update_schedule(
                user_id, **{slot: validate_time(value.strip()), slot + "_enabled": True}
            )
        except ValueError as error:
            context.user_data["editing_time"] = slot
            await self.send(
                chat, esc(error) + "\n" + TIME_PROMPTS[slot], ForceReply(input_field_placeholder="ЧЧ:ММ")
            )
            return
        context.user_data.pop("editing_time", None)
        await self.send(chat, "Время сохранено.")
        await self.show_settings(chat, user_id)

    async def show_topics(self, chat):
        counts = self.dictionary.topics()
        rows = [[Button(SEARCH_LABEL, callback_data="search")]] + [
            [Button(f"{topic} · {counts[topic]}", callback_data=f"topic:{i}:0")]
            for i, topic in enumerate(self.topic_names)
        ]
        await self.send(chat, "Выберите тему. Откройте слово и добавьте его в изучение.", Keyboard(rows))

    async def show_list(self, chat, user_id, query="", topic=None, offset=0, selected=False):
        if selected:
            progress = self.store.selected(user_id, offset=offset)
            words = [
                self.dictionary.words[r["word_id"]] for r in progress if r["word_id"] in self.dictionary.words
            ]
            total = self.store.stats(user_id)["total"]
            prefix = "my"
        else:
            words, total = self.dictionary.search(query, topic, offset=offset)
            prefix = "page" if topic is None else f"topic:{self.topic_names.index(topic)}"
        for word in words:
            self.store.reveal_word(user_id, word["id"])
        rows = [
            [
                Button(
                    f"{'✓ ' if self.store.progress(user_id, w['id']) else ''}{w['word']} — "
                    f"{w['meanings'][0][:45]}",
                    callback_data=f"word:{w['id']}",
                )
            ]
            for w in words
        ]
        navigation = []
        if offset > 0:
            navigation.append(Button("←", callback_data=f"{prefix}:{max(0, offset - 8)}"))
        if offset + 8 < total:
            navigation.append(Button("→", callback_data=f"{prefix}:{offset + 8}"))
        if navigation:
            rows.append(navigation)
        rows.append([Button(SEARCH_LABEL, callback_data="search")])
        rows.append([Button("Темы", callback_data="topics"), Button("Повторить", callback_data="review")])
        await self.send(
            chat,
            f"{esc(topic or ('Мои слова' if selected else 'Результаты'))}: {total}\n"
            + (f"Показаны {offset + 1}–{offset + len(words)}" if words else "Ничего не найдено."),
            Keyboard(rows),
        )

    async def show_word(self, chat, user_id, word_id):
        word = self.dictionary.words.get(word_id)
        if not word:
            await self.send(chat, "Слово отсутствует в текущем словаре.")
            return
        self.store.reveal_word(user_id, word_id)
        progress = self.store.progress(user_id, word_id)
        rows = [[Button("🔊 Произношение", callback_data=f"audio:{word_id}")]]
        picture = Path("data/images") / (word["word"] + ".png")
        if picture.exists():
            rows.append([Button("🖼 Картинка", callback_data=f"image:{word_id}")])
        if word["conjugations"]:
            rows.append([Button("Спряжение", callback_data=f"verbs:{word_id}")])
        if progress is None:
            rows.append([Button("⭐ Учить", callback_data=f"add:{word_id}")])
        elif progress["learned"]:
            pending = self.store.pending(user_id)
            if not pending or pending["word_id"] != word_id:
                rows.append([Button("↩️ Учить заново", callback_data=f"relearn:{word_id}")])
        elif not progress["learned"] and progress["writing_count"] >= 5:
            rows.append(
                [
                    Button(
                        "Отметить выученным", callback_data=f"learned:{word_id}"
                    )
                ]
            )
        rows.append(
            [Button(SEARCH_LABEL, callback_data="search"), Button("Повторить", callback_data="review")]
        )
        text = card_text(word, progress)
        if progress and progress["learned"]:
            due = datetime.fromisoformat(progress["due"]).astimezone(self.config.timezone)
            text += f"\nПроверка {maintenance_label(progress['maintenance_step'])}: {due:%d.%m %H:%M}."
        await self.send(chat, text, Keyboard(rows))

    async def prompt(self, chat, user_id, context, now=None):
        pending = self.store.pending(user_id)
        if not pending:
            if context.user_data.get("session_count", 0) >= self.config.limit:
                await self.send(chat, "Сессия завершена. /review — продолжить, если остались слова.")
                return
            due = self.store.due(user_id, now=now)
            if not due:
                await self.send(
                    chat, "На сейчас повторений нет. /topics — выбрать новые слова; /my — ваш список."
                )
                return
            pending = self.store.challenge(user_id, due[0]["word_id"], now=now)
        word = self.dictionary.words.get(pending["word_id"])
        if word is None:
            await self.send(chat, "Верните прежний словарь: в прогрессе есть отсутствующее слово.")
            return
        text = "✍️ <b>Напишите по-испански</b>\n" + "\n".join(esc(m[:240]) for m in word["meanings"])
        progress = self.store.progress(user_id, word["id"])
        if progress["learned"]:
            text += f"\n🌱 Проверка выученного слова · {maintenance_label(progress['maintenance_step'])}."
        text += f"\nЧасть речи: {esc(word['pos'])}\n\nБез артикля, если он не входит в само выражение."
        rows = [
            [
                Button("Подсказка (не засчитывается)", callback_data=f"hint:{pending['id']}"),
                Button("Не помню", callback_data=f"skip:{pending['id']}"),
            ]
        ]
        await self.send(chat, text, Keyboard(rows))

    async def finish(self, chat, user_id, pending, answer, context, skipped=False):
        word = self.dictionary.words[pending["word_id"]]
        result = self.store.answer(user_id, pending["id"], answer, word, skipped=skipped)
        if not result:
            await self.send(chat, "Этот вопрос уже завершён.")
            return
        context.user_data["session_count"] = context.user_data.get("session_count", 0) + 1
        if result["was_learned"] and result["learned"]:
            text = "✅ Верно! Слово остаётся выученным."
        elif result["correct"]:
            text = "✅ Верно!"
            if result["hinted"]:
                text += " После подсказки написание не засчитывается."
            elif not result["credited"]:
                ready = result["next_credit"].astimezone(self.config.timezone).strftime("%d.%m %H:%M")
                text += f" Написание уже зачтено для этого периода. Следующий зачёт возможен с {ready}."
        else:
            text = "Правильный ответ: <b>" + esc(word["word"]) + "</b>. Вернёмся к нему позже."
        if result["was_learned"] and not result["learned"]:
            text += " Слово снова в частых повторах; новый цикл — 0/5."
        elif result["learned"]:
            text += f"\nРежим: {maintenance_label(result['maintenance_step'])}."
        else:
            text += f"\nЗачтено написаний: {min(result['count'], 5)}/5."
        when = result["due"].astimezone(self.config.timezone).strftime("%d.%m %H:%M")
        text += f"\nСледующее повторение: {when}."
        rows = [
            [Button("Дальше →", callback_data="next"), Button("Карточка", callback_data=f"word:{word['id']}")]
        ]
        if result["learned"]:
            rows.append([Button("↩️ Учить заново", callback_data=f"relearn:{word['id']}")])
        if result["count"] >= 5 and result["correct"] and not result["hinted"] and not result["learned"]:
            rows.append([Button("Отметить выученным", callback_data=f"learned:{word['id']}")])
        await self.send(chat, text, Keyboard(rows))

    async def command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        message = update.effective_message
        if not message or not message.text:
            return
        command = message.text.split()[0].split("@")[0][1:]
        if command == "id" and update.effective_chat.type == "private":
            await message.reply_text(f"Ваш ID: {update.effective_user.id}")
            return
        if not self.authorized(update):
            return
        uid, chat = update.effective_user.id, update.effective_chat
        context.user_data["awaiting_search"] = False
        context.user_data.pop("editing_time", None)
        if command in {"start", "resume"}:
            self.store.subscribe(uid, chat.id)
            await self.send(chat, HELP, HOME_KEYBOARD)
        elif command == "help":
            await self.send(chat, HELP, HOME_KEYBOARD)
        elif command == "topics":
            await self.show_topics(chat)
        elif command == "settings":
            await self.show_settings(chat, uid)
        elif command == "search" and not context.args:
            await self.begin_search(chat, context)
        elif command in {"words", "search", "my"}:
            query = " ".join(context.args) if command == "search" else ""
            context.user_data["search"] = query
            await self.show_list(chat, uid, query=query, selected=command == "my")
        elif command == "review":
            context.user_data["session_count"] = 0
            await self.prompt(chat, uid, context)
        elif command == "cancel":
            pending = self.store.pending(uid)
            if pending:
                await self.finish(chat, uid, pending, "", context, skipped=True)
            else:
                await self.send(chat, "Нет активного вопроса.")
        elif command == "stats":
            stats = self.store.stats(uid)
            await self.send(
                chat,
                f"Выбрано: {stats['total']}\nВыучено: {stats['learned']}\n"
                f"Пора повторить: {stats['due']}\nЗачтено написаний: {stats['writing']}",
            )
        elif command == "pause":
            self.store.pause(uid)
            await self.send(chat, "Ежедневные сообщения выключены. /resume — включить.")
        elif command == "export":
            await chat.send_document(io.BytesIO(self.store.export(uid)), filename="vocabulario-progress.json")

    async def text(self, update, context):
        if not self.authorized(update):
            return
        uid, chat = update.effective_user.id, update.effective_chat
        message = update.effective_message
        if message.text == SEARCH_LABEL:
            context.user_data.pop("editing_time", None)
            await self.begin_search(chat, context)
            return
        if message.text == SETTINGS_LABEL:
            context.user_data["awaiting_search"] = False
            context.user_data.pop("editing_time", None)
            await self.show_settings(chat, uid)
            return
        if message.text == REVIEW_LABEL:
            context.user_data.pop("editing_time", None)
            context.user_data["awaiting_search"] = False
            context.user_data["session_count"] = 0
            await self.prompt(chat, uid, context)
            return
        if message.text == MY_LABEL:
            context.user_data.pop("editing_time", None)
            context.user_data["awaiting_search"] = False
            await self.show_list(chat, uid, selected=True)
            return
        reply = getattr(message, "reply_to_message", None)
        replied_slot = next(
            (
                slot
                for slot, prompt in TIME_PROMPTS.items()
                if reply and reply.text and reply.text.endswith(prompt) and reply.from_user.is_bot
            ),
            None,
        )
        editing_slot = context.user_data.get("editing_time") or replied_slot
        if editing_slot:
            await self.save_time(chat, uid, context, editing_slot, message.text)
            return
        search_reply = bool(reply and reply.text == SEARCH_PROMPT and reply.from_user.is_bot)
        if context.user_data.get("awaiting_search") or search_reply:
            context.user_data["awaiting_search"] = False
            context.user_data["search"] = message.text
            await self.show_list(chat, uid, query=message.text)
            return
        pending = self.store.pending(uid)
        if pending:
            await self.finish(chat, uid, pending, update.effective_message.text, context)
        else:
            context.user_data["search"] = update.effective_message.text
            await self.show_list(chat, uid, query=update.effective_message.text)

    async def callback(self, update, context):
        q = update.callback_query
        authorized = self.authorized(update)
        try:
            await q.answer()
        except TelegramError as error:
            logging.getLogger(__name__).warning("Callback acknowledgement failed: %s", exception_names(error))
        if not authorized:
            return
        uid, chat = update.effective_user.id, update.effective_chat
        parts = q.data.split(":")
        action = parts[0]
        context.user_data["awaiting_search"] = False
        context.user_data.pop("editing_time", None)
        if action == "settings":
            await self.show_settings(chat, uid)
        elif action == "sched_time":
            await self.begin_time_edit(chat, context, parts[1])
        elif action in {"sched_toggle", "sched_day", "sched_days", "sched_pause"}:
            schedule = self.store.schedule(uid)
            try:
                if action == "sched_toggle" and parts[1] in {"morning", "evening"}:
                    field = parts[1] + "_enabled"
                    self.store.update_schedule(uid, **{field: not getattr(schedule, field)})
                elif action == "sched_day":
                    day = int(parts[1])
                    if day not in range(7):
                        return
                    days = set(schedule.weekdays)
                    days.symmetric_difference_update({day})
                    self.store.update_schedule(uid, weekdays=tuple(sorted(days)))
                elif action == "sched_days":
                    presets = {"all": tuple(range(7)), "weekdays": tuple(range(5)), "weekends": (5, 6)}
                    if parts[1] not in presets:
                        return
                    self.store.update_schedule(uid, weekdays=presets[parts[1]])
                elif action == "sched_pause":
                    preference = self.store.preference(uid)
                    if preference and preference["enabled"]:
                        self.store.pause(uid)
                    else:
                        self.store.subscribe(uid, chat.id)
            except ValueError as error:
                await self.send(chat, esc(error))
            await self.show_settings(chat, uid)
        elif action == "search":
            await self.begin_search(chat, context)
        elif action == "topics":
            await self.show_topics(chat)
        elif action in {"review", "next"}:
            if action == "review":
                context.user_data["session_count"] = 0
            await self.prompt(chat, uid, context)
        elif action == "topic":
            await self.show_list(chat, uid, topic=self.topic_names[int(parts[1])], offset=int(parts[2]))
        elif action in {"page", "my"}:
            await self.show_list(
                chat,
                uid,
                query=context.user_data.get("search", ""),
                offset=int(parts[1]),
                selected=action == "my",
            )
        elif action in {"hint", "skip"}:
            pending = self.store.pending(uid)
            if not pending or pending["id"] != parts[1]:
                await self.send(chat, "Эта кнопка относится к уже завершённому вопросу.")
            elif action == "hint":
                self.store.hint(uid, pending["id"])
                await self.show_word(chat, uid, pending["word_id"])
            else:
                await self.finish(chat, uid, pending, "", context, skipped=True)
        elif len(parts) > 1 and parts[1] in self.dictionary.words:
            wid = parts[1]
            word = self.dictionary.words[wid]
            if action == "word":
                await self.show_word(chat, uid, wid)
            elif action == "add":
                added = self.store.add(uid, wid)
                await self.send(chat, "Добавлено! /review — начать." if added else "Слово уже в изучении.")
            elif action == "learned":
                learned = self.store.mark_learned(uid, wid)
                progress = self.store.progress(uid, wid)
                await self.send(
                    chat,
                    "Выучено! Следующая проверка "
                    f"{datetime.fromisoformat(progress['due']).astimezone(self.config.timezone):%d.%m %H:%M} "
                    f"({maintenance_label(progress['maintenance_step'])})."
                    if learned
                    else "Нужны 5 правильных написаний без подсказки: не более 2 за день "
                    "и не чаще одного раза в 8 часов; "
                    "последний ответ верный и нет открытого вопроса по этому слову.",
                )
            elif action == "relearn":
                due = self.store.return_to_learning(uid, wid)
                progress = self.store.progress(uid, wid)
                await self.send(
                    chat,
                    f"Слово снова в частых повторах. Новый цикл: 0/5. "
                    f"Ближайший вопрос: {due.astimezone(self.config.timezone):%d.%m %H:%M}."
                    if due else (
                        "Слово уже в частых повторах."
                        if progress and not progress["learned"]
                        else "Сначала завершите текущий вопрос по этому слову."
                    ),
                )
            elif action in {"audio", "image", "verbs", "tense"}:
                self.store.reveal_word(uid, wid)
                if action == "audio":
                    await self.send(chat, "Готовлю произношение…")
                    try:
                        path = await self.audio.get(word["word"])
                        with path.open("rb") as file:
                            await chat.send_audio(
                                file, title=word["word"], caption="Синтезированная речь · es-ES"
                            )
                    except (TimeoutError, OSError, ClientError, EdgeTTSException, TelegramError) as error:
                        logging.getLogger(__name__).warning("Audio unavailable: %s", type(error).__name__)
                        await self.send(
                            chat, "Сервис озвучивания недоступен. Попробуйте позже. IPA есть в карточке."
                        )
                elif action == "image":
                    path = Path("data/images") / (word["word"] + ".png")
                    if path.exists():
                        with path.open("rb") as file:
                            await chat.send_photo(file, caption=word["word"])
                elif action == "verbs":
                    rows = [
                        [Button(name, callback_data=f"tense:{wid}:{i}")]
                        for i, name in enumerate(word["conjugations"])
                    ]
                    await self.send(chat, "Выберите время и наклонение:", Keyboard(rows))
                else:
                    name = list(word["conjugations"])[int(parts[2])]
                    text = f"<b>{esc(word['word'])} — {esc(name)}</b>\n"
                    text += "\n".join(
                        f"{esc(p)}: <b>{esc(v)}</b>" for p, v in word["conjugations"][name].items()
                    )
                    text += "\n\nFred Jehle / Brian Ghidinelli · CC BY-NC-SA 3.0"
                    await self.send(chat, text)

    async def reminder(self, context):
        preference = self.store.preference(self.config.user_id)
        now = utcnow()
        schedule = self.store.schedule(self.config.user_id)
        if not preference or not preference["enabled"] or not schedule:
            return
        date = now.astimezone(ZoneInfo(schedule.timezone)).date().isoformat()
        slot = due_slot(schedule, now, self.store.delivered(self.config.user_id, date))
        if not slot:
            return
        due = self.store.due(self.config.user_id, now=now)
        if not due and not self.store.pending(self.config.user_id):
            return
        try:
            chat = await context.bot.get_chat(preference["chat_id"])
            label = "🌅 Утреннее" if slot == "morning" else "🌙 Вечернее"
            await self.send(chat, f"{label} повторение испанского. Начинаем!")
            context.user_data["session_count"] = 0
            await self.prompt(chat, self.config.user_id, context, now=now)
            self.store.mark_delivered(self.config.user_id, date, slot)
        except TelegramError as error:
            logging.getLogger(__name__).warning("Reminder failed: %s", exception_names(error))

    async def shutdown(self, application):
        self.store.close()

    async def error(self, update, context):
        logging.getLogger(__name__).error("Update failed: %s", exception_names(context.error))
        if update and self.authorized(update):
            try:
                await self.send(update.effective_chat, "Не удалось выполнить действие. Повторите последнее действие.")
            except TelegramError:
                pass

    def application(self):
        app = (
            Application.builder()
            .token(self.config.token)
            .request(telegram_request())
            .get_updates_request(telegram_request(polling=True))
            .concurrent_updates(False)
            .post_shutdown(self.shutdown)
            .build()
        )
        app.add_handler(
            CommandHandler(
                [
                    "start",
                    "help",
                    "topics",
                    "search",
                    "words",
                    "my",
                    "review",
                    "cancel",
                    "stats",
                    "settings",
                    "pause",
                    "resume",
                    "export",
                    "id",
                ],
                self.command,
            )
        )
        app.add_handler(CallbackQueryHandler(self.callback))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.text))
        app.add_error_handler(self.error)
        app.job_queue.run_repeating(
            self.reminder, interval=60, first=5, user_id=self.config.user_id, name="daily-review"
        )
        return app


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    try:
        bot = Bot(Config.load())
    except (ValueError, FileNotFoundError) as error:
        raise SystemExit(str(error)) from None
    if not bot.config.user_id:
        logging.getLogger(__name__).warning("ALLOWED_USER_ID пуст: доступна только команда /id.")
    bot.application().run_polling(allowed_updates=["message", "callback_query"], bootstrap_retries=3)


if __name__ == "__main__":
    main()
