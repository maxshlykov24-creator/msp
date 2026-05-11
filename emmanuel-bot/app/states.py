from aiogram.fsm.state import State, StatesGroup


class HashtagStates(StatesGroup):
    waiting_hashtag = State()


class WeeklyReportStates(StatesGroup):
    q1_prayer = State()
    q2_sermons = State()
    q3_fast = State()
    q4_help = State()
    q5_revelations = State()
    q6_meetings = State()
    q7_plan_pct = State()
    q8_next_plan = State()
    confirm = State()
    editing = State()
