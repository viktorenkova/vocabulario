import json
import sqlite3
import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from fsrs import Card, Rating, Scheduler

from .dictionary import normalize
from .schedule import Schedule


def utcnow():
    return datetime.now(UTC)


MIN_WRITING_GAP = timedelta(hours=8)
MAX_WRITINGS_PER_DAY = 2


class Store:
    def __init__(self, path: Path, retention=0.9):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.scheduler = Scheduler(
            desired_retention=retention,
            enable_fuzzing=False,
            learning_steps=(timedelta(days=1),),
            relearning_steps=(timedelta(minutes=10),),
            maximum_interval=365,
        )
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS words (
                user_id INTEGER NOT NULL, word_id TEXT NOT NULL, card TEXT NOT NULL,
                due TEXT NOT NULL, writing_count INTEGER NOT NULL DEFAULT 0,
                last_credit TEXT, learned INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, word_id));
            CREATE INDEX IF NOT EXISTS words_due ON words(user_id, due);
            CREATE TABLE IF NOT EXISTS challenges (
                id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, word_id TEXT NOT NULL,
                hinted INTEGER NOT NULL DEFAULT 0, done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL);
            CREATE UNIQUE INDEX IF NOT EXISTS one_pending ON challenges(user_id) WHERE done=0;
            CREATE TABLE IF NOT EXISTS reviews (
                challenge_id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, word_id TEXT NOT NULL,
                reviewed_at TEXT NOT NULL, correct INTEGER NOT NULL, credited INTEGER NOT NULL,
                hinted INTEGER NOT NULL, rating INTEGER NOT NULL, log TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS preferences (
                user_id INTEGER PRIMARY KEY, chat_id INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1, last_reminder TEXT);
            CREATE TABLE IF NOT EXISTS reminder_schedule (
                user_id INTEGER PRIMARY KEY, morning TEXT NOT NULL, evening TEXT NOT NULL,
                weekdays TEXT NOT NULL, timezone TEXT NOT NULL,
                morning_enabled INTEGER NOT NULL DEFAULT 1, evening_enabled INTEGER NOT NULL DEFAULT 1);
            CREATE TABLE IF NOT EXISTS reminder_deliveries (
                user_id INTEGER NOT NULL, local_date TEXT NOT NULL, slot TEXT NOT NULL,
                PRIMARY KEY(user_id, local_date, slot));
        """)
        self._migrate_writing_credits()

    def _zone(self, user_id):
        schedule = self.schedule(user_id)
        return ZoneInfo(schedule.timezone) if schedule else UTC

    @staticmethod
    def _credit_ready_at(now, last_credit, daily_count, zone):
        ready = now
        if last_credit:
            ready = max(ready, last_credit + MIN_WRITING_GAP)
        if daily_count >= MAX_WRITINGS_PER_DAY:
            local = now.astimezone(zone)
            next_day = datetime.combine(local.date() + timedelta(days=1), time.min, zone)
            ready = max(ready, next_day.astimezone(UTC))
        return ready

    def _daily_credits(self, user_id, word_id, now, zone):
        local = now.astimezone(zone)
        start = datetime.combine(local.date(), time.min, zone).astimezone(UTC)
        end = datetime.combine(local.date() + timedelta(days=1), time.min, zone).astimezone(UTC)
        return self.db.execute(
            "SELECT COUNT(*) FROM reviews WHERE user_id=? AND word_id=? AND credited=1 "
            "AND reviewed_at>=? AND reviewed_at<?",
            (user_id, word_id, start.isoformat(), end.isoformat()),
        ).fetchone()[0]

    def _migrate_writing_credits(self):
        if self.db.execute("PRAGMA user_version").fetchone()[0] >= 1:
            return
        # Old versions kept morning/evening answers but credited only one per 24 hours.
        rows = self.db.execute(
            "SELECT challenge_id,user_id,word_id,reviewed_at,correct,hinted,credited "
            "FROM reviews ORDER BY reviewed_at,challenge_id"
        ).fetchall()
        states = {}
        zones = {}
        with self.db:
            for row in rows:
                key = (row["user_id"], row["word_id"])
                if key not in states:
                    states[key] = [None, {}, 0]
                last, daily, count = states[key]
                now = datetime.fromisoformat(row["reviewed_at"])
                zone = zones.setdefault(row["user_id"], self._zone(row["user_id"]))
                day = now.astimezone(zone).date()
                credited = bool(
                    row["correct"] and not row["hinted"]
                    and self._credit_ready_at(now, last, daily.get(day, 0), zone) == now
                )
                if credited:
                    daily[day] = daily.get(day, 0) + 1
                    states[key] = [now, daily, count + 1]
                if credited != bool(row["credited"]):
                    self.db.execute(
                        "UPDATE reviews SET credited=? WHERE challenge_id=?",
                        (int(credited), row["challenge_id"]),
                    )
            for (user_id, word_id), (last, _, count) in states.items():
                progress = self.progress(user_id, word_id)
                if progress and count > progress["writing_count"]:
                    self.db.execute(
                        "UPDATE words SET writing_count=?,last_credit=? WHERE user_id=? AND word_id=?",
                        (count, last.isoformat(), user_id, word_id),
                    )
            self.db.execute("PRAGMA user_version=1")

    def ensure_schedule(self, user_id, schedule):
        with self.db:
            inserted = self.db.execute(
                "INSERT OR IGNORE INTO reminder_schedule VALUES (?,?,?,?,?,?,?)",
                (
                    user_id,
                    schedule.morning,
                    schedule.evening,
                    json.dumps(schedule.weekdays),
                    schedule.timezone,
                    int(schedule.morning_enabled),
                    int(schedule.evening_enabled),
                ),
            ).rowcount
            if inserted:
                previous = self.preference(user_id)
                if previous and previous["last_reminder"]:
                    self.db.execute(
                        "INSERT OR IGNORE INTO reminder_deliveries VALUES (?,?,?)",
                        (user_id, previous["last_reminder"], "morning"),
                    )
        return bool(inserted)

    def schedule(self, user_id):
        row = self.db.execute("SELECT * FROM reminder_schedule WHERE user_id=?", (user_id,)).fetchone()
        if row is None:
            return None
        return Schedule(
            row["morning"],
            row["evening"],
            tuple(json.loads(row["weekdays"])),
            row["timezone"],
            bool(row["morning_enabled"]),
            bool(row["evening_enabled"]),
        )

    def update_schedule(self, user_id, **changes):
        current = self.schedule(user_id)
        if current is None:
            raise ValueError("Расписание ещё не настроено. Отправьте /start.")
        changed = replace(current, **changes)
        with self.db:
            self.db.execute(
                """UPDATE reminder_schedule SET morning=?,evening=?,weekdays=?,timezone=?,
                morning_enabled=?,evening_enabled=? WHERE user_id=?""",
                (
                    changed.morning,
                    changed.evening,
                    json.dumps(changed.weekdays),
                    changed.timezone,
                    int(changed.morning_enabled),
                    int(changed.evening_enabled),
                    user_id,
                ),
            )
            # Keep active learning aligned when the owner changes the next session.
            next_session = changed.next_session(utcnow())
            if next_session:
                rows = self.db.execute(
                    "SELECT word_id,card,due FROM words WHERE user_id=? AND learned=0", (user_id,)
                ).fetchall()
                for row in rows:
                    if datetime.fromisoformat(row["due"]) > next_session:
                        card = Card.from_json(row["card"])
                        card.due = next_session
                        self.db.execute(
                            "UPDATE words SET card=?,due=? WHERE user_id=? AND word_id=?",
                            (card.to_json(), card.due.isoformat(), user_id, row["word_id"]),
                        )
        return changed

    def preference(self, user_id):
        return self.db.execute("SELECT * FROM preferences WHERE user_id=?", (user_id,)).fetchone()

    def delivered(self, user_id, local_date):
        return {
            row["slot"]: row["local_date"]
            for row in self.db.execute(
                "SELECT slot,local_date FROM reminder_deliveries WHERE user_id=? AND local_date=?",
                (user_id, local_date),
            )
        }

    def mark_delivered(self, user_id, local_date, slot):
        with self.db:
            self.db.execute(
                "INSERT OR IGNORE INTO reminder_deliveries VALUES (?,?,?)", (user_id, local_date, slot)
            )
            self.db.execute("UPDATE preferences SET last_reminder=? WHERE user_id=?", (local_date, user_id))

    def subscribe(self, user_id, chat_id):
        with self.db:
            self.db.execute(
                """INSERT INTO preferences(user_id,chat_id) VALUES (?,?)
                ON CONFLICT(user_id) DO UPDATE SET chat_id=excluded.chat_id,enabled=1""",
                (user_id, chat_id),
            )

    def pause(self, user_id):
        with self.db:
            self.db.execute("UPDATE preferences SET enabled=0 WHERE user_id=?", (user_id,))

    def add(self, user_id, word_id, now=None):
        now = now or utcnow()
        card = Card(due=now)
        with self.db:
            cur = self.db.execute(
                "INSERT OR IGNORE INTO words(user_id,word_id,card,due) VALUES (?,?,?,?)",
                (user_id, word_id, card.to_json(), now.isoformat()),
            )
        return cur.rowcount == 1

    def progress(self, user_id, word_id):
        return self.db.execute(
            "SELECT * FROM words WHERE user_id=? AND word_id=?", (user_id, word_id)
        ).fetchone()

    def due(self, user_id, now=None, limit=15):
        return self.db.execute(
            "SELECT * FROM words WHERE user_id=? AND due<=? ORDER BY due LIMIT ?",
            (user_id, (now or utcnow()).isoformat(), limit),
        ).fetchall()

    def selected(self, user_id, offset=0, limit=8):
        return self.db.execute(
            "SELECT * FROM words WHERE user_id=? ORDER BY learned,due LIMIT ? OFFSET ?",
            (user_id, limit, offset),
        ).fetchall()

    def pending(self, user_id):
        return self.db.execute("SELECT * FROM challenges WHERE user_id=? AND done=0", (user_id,)).fetchone()

    def challenge(self, user_id, word_id, now=None):
        pending = self.pending(user_id)
        if pending:
            return pending
        if not self.progress(user_id, word_id):
            raise ValueError("Слово не выбрано для изучения")
        with self.db:
            self.db.execute(
                "INSERT INTO challenges(id,user_id,word_id,created_at) VALUES (?,?,?,?)",
                (uuid.uuid4().hex[:16], user_id, word_id, (now or utcnow()).isoformat()),
            )
        return self.pending(user_id)

    def hint(self, user_id, challenge_id):
        with self.db:
            cur = self.db.execute(
                "UPDATE challenges SET hinted=1 WHERE user_id=? AND id=? AND done=0", (user_id, challenge_id)
            )
        return cur.rowcount == 1

    def reveal_word(self, user_id, word_id):
        # Opening a dictionary card during a test also reveals the answer.
        pending = self.pending(user_id)
        if pending and pending["word_id"] == word_id:
            self.hint(user_id, pending["id"])

    def answer(self, user_id, challenge_id, answer, word, now=None, skipped=False):
        now = now or utcnow()
        with self.db:
            challenge = self.db.execute(
                "SELECT * FROM challenges WHERE id=? AND user_id=? AND done=0", (challenge_id, user_id)
            ).fetchone()
            if not challenge or challenge["word_id"] != word["id"]:
                return None
            progress = self.progress(user_id, word["id"])
            correct = not skipped and normalize(answer) == normalize(word["word"])
            unaided = correct and not challenge["hinted"]
            last_credit = datetime.fromisoformat(progress["last_credit"]) if progress["last_credit"] else None
            zone = self._zone(user_id)
            daily_count = self._daily_credits(user_id, word["id"], now, zone)
            next_credit = self._credit_ready_at(now, last_credit, daily_count, zone)
            credited = unaided and next_credit == now
            count = progress["writing_count"] + int(credited)
            rating = Rating.Good if unaided else Rating.Again
            card, log = self.scheduler.review_card(
                Card.from_json(progress["card"]), rating, review_datetime=now
            )
            learned = bool(progress["learned"]) and unaided
            # Product policy: frequent practice until the writing gate, monthly maintenance after mastery.
            if unaided and not learned:
                schedule = self.schedule(user_id)
                next_session = schedule.next_session(now) if schedule else None
                if next_session:
                    card.due = min(card.due, next_session)
                elif count < 5:
                    card.due = min(card.due, now + timedelta(days=1))
                if not credited:
                    card.due = max(card.due, next_credit)
            if learned:
                card.due = max(card.due, now + timedelta(days=30))
            self.db.execute(
                """UPDATE words SET card=?,due=?,writing_count=?,last_credit=?,learned=?
                WHERE user_id=? AND word_id=?""",
                (
                    card.to_json(),
                    card.due.isoformat(),
                    count,
                    now.isoformat() if credited else progress["last_credit"],
                    int(learned),
                    user_id,
                    word["id"],
                ),
            )
            self.db.execute("UPDATE challenges SET done=1 WHERE id=?", (challenge_id,))
            self.db.execute(
                "INSERT INTO reviews VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    challenge_id,
                    user_id,
                    word["id"],
                    now.isoformat(),
                    int(correct),
                    int(credited),
                    challenge["hinted"],
                    int(rating),
                    log.to_json(),
                ),
            )
        return {
            "correct": correct,
            "credited": credited,
            "hinted": bool(challenge["hinted"]),
            "count": count,
            "due": card.due,
            "learned": learned,
            "next_credit": next_credit if not credited and unaided else None,
        }

    def mark_learned(self, user_id, word_id, now=None):
        now = now or utcnow()
        progress = self.progress(user_id, word_id)
        if not progress or progress["writing_count"] < 5:
            return False
        if progress["learned"]:
            return True
        last = self.db.execute(
            "SELECT correct,hinted FROM reviews WHERE user_id=? AND word_id=? "
            "ORDER BY reviewed_at DESC LIMIT 1",
            (user_id, word_id),
        ).fetchone()
        if not last or not last["correct"] or last["hinted"]:
            return False
        pending = self.pending(user_id)
        if pending and pending["word_id"] == word_id:
            return False
        card = Card.from_json(progress["card"])
        card.due = max(card.due, now + timedelta(days=30))
        with self.db:
            self.db.execute(
                "UPDATE words SET learned=1,card=?,due=? WHERE user_id=? AND word_id=?",
                (card.to_json(), card.due.isoformat(), user_id, word_id),
            )
        return True

    def stats(self, user_id, now=None):
        return dict(
            self.db.execute(
                """SELECT COUNT(*) total, COALESCE(SUM(learned),0) learned,
            COALESCE(SUM(due<=?),0) due, COALESCE(SUM(writing_count),0) writing
            FROM words WHERE user_id=?""",
                ((now or utcnow()).isoformat(), user_id),
            ).fetchone()
        )

    def export(self, user_id):
        return json.dumps(
            {
                "schedule": asdict(self.schedule(user_id)) if self.schedule(user_id) else None,
                "words": [
                    dict(r) for r in self.db.execute("SELECT * FROM words WHERE user_id=?", (user_id,))
                ],
                "reviews": [
                    dict(r) for r in self.db.execute("SELECT * FROM reviews WHERE user_id=?", (user_id,))
                ],
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8")

    def close(self):
        self.db.close()
