import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

DAY_NAMES = ("Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс")


def validate_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{2}:[0-9]{2}", value):
        raise ValueError("Введите время в формате ЧЧ:ММ, например 08:30.")
    hour, minute = map(int, value.split(":"))
    if not 0 <= hour <= 23 or not 0 <= minute <= 59:
        raise ValueError("Часы должны быть от 00 до 23, минуты — от 00 до 59.")
    return value


@dataclass(frozen=True)
class Schedule:
    morning: str = "09:00"
    evening: str = "20:00"
    weekdays: tuple[int, ...] = tuple(range(7))
    timezone: str = "Europe/Moscow"
    morning_enabled: bool = True
    evening_enabled: bool = True

    def __post_init__(self):
        validate_time(self.morning)
        validate_time(self.evening)
        ZoneInfo(self.timezone)
        if any(day not in range(7) for day in self.weekdays) or len(set(self.weekdays)) != len(self.weekdays):
            raise ValueError("Некорректные дни недели.")
        if self.morning_enabled and self.evening_enabled and self.morning == self.evening:
            raise ValueError("Утреннее и вечернее занятия должны быть в разное время.")

    def sessions(self):
        result = []
        if self.morning_enabled:
            result.append(("morning", self.morning))
        if self.evening_enabled:
            result.append(("evening", self.evening))
        return sorted(result, key=lambda item: item[1])

    def next_session(self, now):
        local = now.astimezone(ZoneInfo(self.timezone))
        for offset in range(8):
            date = local.date() + timedelta(days=offset)
            if date.weekday() not in self.weekdays:
                continue
            for _, clock in self.sessions():
                hour, minute = map(int, clock.split(":"))
                candidate = datetime(
                    date.year, date.month, date.day, hour, minute, tzinfo=ZoneInfo(self.timezone)
                ).astimezone(UTC)
                if candidate > now:
                    return candidate
        return None


def due_slot(schedule, now, delivered):
    """Only the most recent session is eligible; missed sessions are not sent in a burst."""
    local = now.astimezone(ZoneInfo(schedule.timezone))
    if local.weekday() not in schedule.weekdays:
        return None
    current = local.strftime("%H:%M")
    eligible = [slot for slot, clock in schedule.sessions() if clock <= current]
    if not eligible:
        return None
    slot = eligible[-1]
    return None if delivered.get(slot) == local.date().isoformat() else slot
