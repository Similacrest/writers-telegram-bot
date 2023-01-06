from datetime import datetime, timedelta
from enum import StrEnum

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from tqdm import tqdm

import utils

MIN_SPRINT = 1
DEFAULT_SPRINT = 30
MAX_SPRINT = 120
DEFAULT_SPRINT_DELAY = 2

class SprintStatus(StrEnum):
    Initialized = "створено"
    Planned = "почнеться за "
    Running = "розпочався"
    Finished = "завершено"
    Cancelled = "скасовано"
    CancelledWhilePlanned = "скасовано до його початку"



TENSES_FOR_WRITE_VERB = {
    SprintStatus.Initialized: "Писатимемо",
    SprintStatus.Planned: "Писатимемо",
    SprintStatus.Running: "Пишемо",
    SprintStatus.Finished: "Писали",
    SprintStatus.Cancelled: "Писали всього",
    SprintStatus.CancelledWhilePlanned: "Не писали"
}

class Sprint:
    def __init__(self, duration, delay=DEFAULT_SPRINT_DELAY):
        self.message = None
        self.original_duration = duration
        self.duration = duration
        self.delay=delay
        self.status = SprintStatus.Initialized
        self.start_date = None
        self.end_date = None
        self.users = []

    async def plan_sprint(self, start_command_message, user=None):
            if user is None:
                user = start_command_message.from_user
            if self.status == SprintStatus.Initialized:
                self.status = SprintStatus.Planned
                self.start_date = datetime.now() + timedelta(minutes=self.delay)
                self.end_date = self.start_date + timedelta(minutes=self.duration)
                self.users = [user]
                self.message = await start_command_message.reply_html(**self.render_message())

    async def start_sprint(self):
        if self.status == SprintStatus.Planned:
            self.status = SprintStatus.Running
            old_message = self.message
            self.message = await old_message.reply_html(**self.render_message())
            await old_message.delete()
            try:
                await self.message.pin(disable_notification=True)
            except BadRequest:
                pass
    async def add_user(self, callback_query):
        if (self.status in (SprintStatus.Planned, SprintStatus.Running)):
            if callback_query.from_user in self.users:
                await callback_query.answer("Ви вже у спринті!")
            else:
                self.users.append(callback_query.from_user)
                await self.edit_message() 
                await callback_query.answer("Додано до спринту!")

        else:
            callback_query.answer("Ви не можете додатися до цього спринту.")

    async def remove_user(self, user):
        if self.status in (SprintStatus.Planned, SprintStatus.Running):
            self.users.remove(user)
            await self.edit_message()
    
    async def cancel_sprint(self):
            self.job.schedule_removal()
            if self.status == SprintStatus.Planned:
                self.status = SprintStatus.CancelledWhilePlanned
            if self.status == SprintStatus.Running:
                self.end_date = datetime.now()
                self.duration = max(0, int((self.end_date - self.start_date).total_seconds() // 60))
                self.status = SprintStatus.Cancelled
            await self.edit_message()
            try:
                await self.message.unpin()
            except BadRequest:
                pass


    def render_message(self):
        if self.status in (SprintStatus.Planned, SprintStatus.Running):
            keyboard = [[
                InlineKeyboardButton("Долучитися", callback_data='join_sprint'),
                InlineKeyboardButton("Вийти/скасувати", callback_data='leave_or_cancel_sprint'),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        elif self.status in (SprintStatus.Cancelled, SprintStatus.CancelledWhilePlanned):
            keyboard = [[
                InlineKeyboardButton("Повторити", callback_data=f'repeat_last_sprint_{self.original_duration}'),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None

        formatted_duration = utils.format_numeral_nouns(self.duration, ('хвилина', 'хвилини', 'хвилин'))
        if self.status == SprintStatus.Planned:
            planned_start = utils.format_numeral_nouns(self.delay, ('хвилина', 'хвилини', 'хвилин'))
        else:
            planned_start = ""

        message = f"""<b>Спринт {self.status.value}{planned_start}</b>!\n
{TENSES_FOR_WRITE_VERB[self.status]} <b>{formatted_duration}</b>, з <b>{self.start_date:%H:%M}</b> до <b>{self.end_date:%H:%M}</b>.\n
<b>Учасники: </b>"""
        message += ", ".join(u.mention_html() for u in self.users)
        elapsed_duration = (datetime.now() - self.start_date).total_seconds()
        if self.status == SprintStatus.Running:
            message += "\n" + tqdm.format_meter(n=int(elapsed_duration // 60), total=self.duration, elapsed=elapsed_duration,
                                            ncols=30, bar_format="{percentage:2.0f}%<code>|{bar}|</code>")
            message += f" ще {int(self.duration-elapsed_duration//60)} хв."

        return {'text': message, 'reply_markup': reply_markup}

    async def edit_message(self):
        try:
            await self.message.edit_text(**self.render_message())
        except BadRequest:
            # Message is the same as before
            pass

    async def end_sprint(self):
        if self.status == SprintStatus.Running:
            await self.edit_message()
            self.status = SprintStatus.Finished
            try:
                await self.message.unpin()
            except BadRequest:
                pass
            self.message = await self.message.reply_html(**self.render_message())
            self.job.schedule_removal()
        else:
            raise AttributeError("Cannot end sprint before starting!")
    
    async def leave_or_cancel_sprint(self, callback_query):
        if self.status in (SprintStatus.Planned, SprintStatus.Running):
            if callback_query.from_user in self.users and len(self.users) > 1:
                await self.remove_user(callback_query.from_user)
                await callback_query.answer("Вас вилучено зі спринту")
            elif ((callback_query.from_user in self.users) and (len(self.users) == 1)) \
                or (callback_query.from_user.id in [admin.user.id for admin in await callback_query.message.chat.get_administrators()]):
                await self.cancel_sprint()
                await callback_query.answer("Спринт скасовано")
            else:
                await callback_query.answer("Ви не маєте права скасувати спринт")



    async def tick(self, context):
        if self.status == SprintStatus.Planned:
            await self.start_sprint()
        if (self.status == SprintStatus.Running) and ((self.end_date - datetime.now()).total_seconds() < 30):
            await self.end_sprint()
        await self.edit_message()