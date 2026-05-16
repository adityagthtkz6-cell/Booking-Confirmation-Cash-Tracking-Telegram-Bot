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


def get_session(user_id: int) -> Optional[BookingSession]:
    return _sessions.get(user_id)


def set_session(user_id: int, session: BookingSession) -> None:
    _sessions[user_id] = session


def clear_session(user_id: int) -> None:
    _sessions.pop(user_id, None)
