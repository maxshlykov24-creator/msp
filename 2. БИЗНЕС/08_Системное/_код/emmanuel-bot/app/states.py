from aiogram.fsm.state import State, StatesGroup


class HashtagStates(StatesGroup):
    waiting_hashtag = State()


class WeeklyReportStates(StatesGroup):
    q1_prayer = State()
    q1b_bible = State()
    q2_sermons = State()
    q6_meetings = State()
    q3_fast = State()
    q4_help = State()
    q5_revelations = State()
    confirm = State()
    editing = State()
