from collections import deque
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Optional


class Step(Enum):
    ADULTS = auto()
    KIDS = auto()
    AMOUNT = auto()
    PAYMENT = auto()
    RECEIPT = auto()


@dataclass
class BookingSession:
    booking_number: str
    tour_name: str
    booking_date: str
    row_index: int
    step: Step = Step.ADULTS
    adults: Optional[int] = None
    kids: Optional[int] = None
    amount: Optional[float] = None
    payment_method: Optional[str] = None


_sessions: dict[int, BookingSession] = {}
_queues: dict[int, deque] = {}


def get_session(user_id: int) -> Optional[BookingSession]:
    return _sessions.get(user_id)


def set_session(user_id: int, session: BookingSession) -> None:
    _sessions[user_id] = session


def clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)


def enqueue_session(user_id: int, session: BookingSession) -> None:
    if user_id not in _queues:
        _queues[user_id] = deque()
    _queues[user_id].append(session)


def dequeue_next(user_id: int) -> Optional[BookingSession]:
    q = _queues.get(user_id)
    return q.popleft() if q else None


def clear_queue(user_id: int) -> None:
    _queues.pop(user_id, None)


def queue_size(user_id: int) -> int:
    return len(_queues.get(user_id, []))
