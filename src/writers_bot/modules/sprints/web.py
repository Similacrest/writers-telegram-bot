"""The sprints module's part of the web app: the sprint panel, your works, and your progress.

Richer input than chat allows: a field per work, toggles, goals. The same core does the work, so a report
from the panel and one from a chat reply are the same thing.
"""

from datetime import UTC, date, timedelta
from typing import TYPE_CHECKING

from aiohttp import web

from ...core import sprint as sp
from ...core.board import confirmation
from ...core.days import bot_day, fillable
from ...core.fmt import PlainFormatter
from ...core.i18n import t
from ...core.models import Goal, GoalPeriod, Outcome, Sprint, Unit, Work
from ...core.people import CAN_DM, HIDE_NUMBERS
from ...core.reporting import ChatOptions, Status
from ...core.reportparse import parse
from ...core.sources import find_url
from ...core.stats import history
from ...core.works import WorkError
from ...web.auth import AuthError, WebUser
from ...web.server import ApiError, WebApp, ok

if TYPE_CHECKING:
    from .telegram import Module

PLAIN = PlainFormatter(UTC)


def _ref(user: WebUser) -> sp.UserRef:
    return sp.UserRef(user.user_id, user.name)


def work_json(w: Work, sprint_work=None) -> dict:
    return {
        "id": w.id,
        "name": w.name,
        "unit": w.unit.value,
        "url": w.url,
        "hide_title": w.hide_title,
        "keep": w.keep,
        "total": w.total,
        "goal": {"amount": w.goal.amount, "period": w.goal.period.value} if w.goal else None,
        "in_sprint": sprint_work is not None,
        "amount": sprint_work.amount if sprint_work is not None else None,
    }


def routes(module: "Module", app: WebApp) -> None:
    tbot = module.bot
    engine, rep = module.engine, module.rep
    works = tbot.services.works
    people = tbot.people

    async def person(user: WebUser) -> int:
        return await people.person_id(user.platform, user.user_id, user.name)

    def check_platform(user: WebUser) -> None:
        if user.platform != "telegram":
            raise ApiError(403, "web.error_platform")

    async def find_sprint(request: web.Request, user: WebUser) -> Sprint:
        sprint = engine.get(int(request.match_info["sprint"]))
        if sprint is None:
            raise ApiError(404, "web.error_sprint_over")
        await app.require_member(user, int(sprint.key.chat_id))
        return sprint

    async def sprint_json(sprint: Sprint, user: WebUser) -> dict:
        p = sprint.participants.get(user.user_id)
        opts = ChatOptions.from_refs(sprint.refs)
        mine = []
        if p is not None and p.person_id is not None:
            all_works = await works.for_person(p.person_id) if module.works_on else []
            ids = {w.id for w in all_works}
            mine = [work_json(w, p.works.get(w.id)) for w in all_works if opts.percent or w.unit == Unit.WORDS]
            for sw in p.works.values():
                if sw.work_id not in ids:  # the unnamed work
                    mine.append(
                        {"id": sw.work_id, "name": None, "unit": sw.unit.value, "in_sprint": True, "amount": sw.amount}
                    )
        return {
            "id": sprint.id,
            "status": sprint.status.value,
            "start": sprint.start_at.isoformat(),
            "end": sprint.end_at.isoformat(),
            "report_until": sprint.report_until.isoformat() if sprint.report_until else None,
            "duration": sprint.duration,
            "joined": p is not None,
            "accepting": engine.accepting(sprint, user.user_id),
            "outcome": p.outcome.value if p and p.outcome else None,
            "works_on": opts.works,
            "percent_on": opts.percent,
            "works": mine,
            "people": len(sprint.participants),
        }

    async def get_sprint(request: web.Request, user: WebUser) -> web.Response:
        check_platform(user)
        sprint = None
        launch = request.query.get("launch")
        if launch:
            try:
                chat_id, sprint_id = app.signer.check_launch(launch)
            except AuthError:
                raise ApiError(400, "web.error_link") from None
            await app.require_member(user, chat_id)
            sprint = engine.get(sprint_id)
            if sprint is None or sprint.key.chat_id != str(chat_id):
                return web.json_response({"sprint": None})
        else:
            sprint = engine.current_for(user.user_id) or engine.find(
                lambda s: s.is_active and user.user_id in s.participants
            )
        return web.json_response({"sprint": await sprint_json(sprint, user) if sprint else None})

    async def join(request: web.Request, user: WebUser) -> web.Response:
        sprint = await find_sprint(request, user)
        result, _ = await engine.join(sprint.key, _ref(user), sprint.id, prepare=rep.preparer(_ref(user)))
        if result == sp.JoinResult.CLOSED:
            raise ApiError(409, "web.error_sprint_over")
        return web.json_response({"sprint": await sprint_json(sprint, user)})

    async def toggle(request: web.Request, user: WebUser) -> web.Response:
        sprint = await find_sprint(request, user)
        body = await app.body(request)
        state = await rep.toggle_work(sprint, user.user_id, int(body.get("work_id", 0)))
        if state is None:
            raise ApiError(404, "web.error_bad_request")
        return web.json_response({"sprint": await sprint_json(sprint, user)})

    async def report(request: web.Request, user: WebUser) -> web.Response:
        sprint = await find_sprint(request, user)
        body = await app.body(request)
        messages = []
        today = bot_day(tbot.now())
        for entry in body.get("entries") or []:
            text = str(entry.get("text", "")).strip()
            if not text:
                continue
            parsed = parse(text, find_url, today=today, allow_day=False)
            if parsed is None:
                raise ApiError(400, "report.nothing")
            work_id = entry.get("work_id")
            choice = {i: int(work_id) for i in range(len(parsed.items))} if work_id else None
            result = await rep.report(sprint, _ref(user), parsed, choice)
            if not result.ok:
                raise _result_error(result)
            messages.append(confirmation(PLAIN, result.changes, result.outcome))
        return web.json_response({"sprint": await sprint_json(sprint, user), "message": " ".join(messages)})

    async def outcome(request: web.Request, user: WebUser) -> web.Response:
        sprint = await find_sprint(request, user)
        body = await app.body(request)
        value = Outcome.DONE if body.get("outcome") == "done" else Outcome.FAILED
        result = await rep.set_outcome(sprint, _ref(user), value)
        if not result.ok:
            raise ApiError(409, _error_key(result.status))
        return web.json_response({"sprint": await sprint_json(sprint, user)})

    # --- works -----------------------------------------------------------------------------------

    async def list_works(_request: web.Request, user: WebUser) -> web.Response:
        pid = await person(user)
        settings = await people.settings(pid)
        return web.json_response(
            {
                "works": [work_json(w) for w in await works.for_person(pid)],
                "percent_on": module.percent_on,
                "hide_numbers": settings.get(HIDE_NUMBERS) == "1",
                "can_dm": settings.get(CAN_DM) == "1",
                "bot": tbot.me.username if tbot.me else None,
            }
        )

    async def own(request: web.Request, user: WebUser) -> Work:
        work = await works.get(int(request.match_info["work"]))
        if work is None or work.unnamed or work.person_id != await person(user):
            raise ApiError(404, "web.error_bad_request")
        return work

    async def add_work(request: web.Request, user: WebUser) -> web.Response:
        body = await app.body(request)
        pid = await person(user)
        unit = Unit(body["unit"]) if module.percent_on and body.get("unit") in Unit._value2member_map_ else Unit.WORDS
        try:
            work = await works.create(pid, str(body.get("name", "")), unit)
        except WorkError as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(work_json(work))

    async def update_work(request: web.Request, user: WebUser) -> web.Response:
        work = await own(request, user)
        body = await app.body(request)
        try:
            if "name" in body:
                work = await works.rename(work, str(body["name"]))
            if "unit" in body and module.percent_on and body["unit"] in Unit._value2member_map_:
                work = await works.set_unit(work, Unit(body["unit"]))
            for flag in ("hide_title", "keep"):
                if flag in body:
                    work = await works.set_flag(work, flag, bool(body[flag]))
            if "url" in body:
                url = find_url(str(body["url"] or ""))
                if body["url"] and url is None:
                    return web.json_response({"error": t("works.not_a_link")}, status=400)
                count = None
                if url and tbot.links is not None:
                    try:
                        count = await tbot.links.count(url)
                    except Exception as e:
                        return web.json_response({"error": t("common.link_error", error=e)}, status=400)
                work = await works.set_link(work, url, count)
            if "goal" in body:
                goal = body["goal"]
                if goal:
                    if (await people.settings(work.person_id)).get(CAN_DM) != "1":
                        raise ApiError(409, "web.error_dm_first")
                    goal = Goal(int(goal["amount"]), GoalPeriod(goal["period"]))
                work = await works.set_goal(work, goal or None)
        except WorkError as e:
            return web.json_response({"error": str(e)}, status=400)
        except (KeyError, ValueError, TypeError):
            raise ApiError(400, "web.error_bad_request") from None
        await rep.refresh_work(work)
        return web.json_response(work_json(work))

    async def delete_work(request: web.Request, user: WebUser) -> web.Response:
        await works.delete(await own(request, user))
        return ok()

    async def set_privacy(request: web.Request, user: WebUser) -> web.Response:
        body = await app.body(request)
        pid = await person(user)
        hide = bool(body.get("hide_numbers"))
        await people.set(pid, HIDE_NUMBERS, "1" if hide else None)
        await rep.refresh_privacy(pid, hide)
        return ok({"hide_numbers": hide})

    # --- progress --------------------------------------------------------------------------------

    async def progress(request: web.Request, user: WebUser) -> web.Response:
        body = await app.body(request)
        today = bot_day(tbot.now())
        parsed = parse(str(body.get("text", "")), find_url, today=today, allow_day=True)
        if parsed is None:
            raise ApiError(400, "progress.usage")
        if body.get("day"):
            try:
                parsed.day = date.fromisoformat(body["day"])
            except ValueError:
                raise ApiError(400, "web.error_bad_request") from None
        work_id = body.get("work_id")
        choice = {i: int(work_id) for i in range(len(parsed.items))} if work_id else None
        result = await rep.progress(_ref(user), parsed, choice=choice, percent=module.percent_on)
        if result.status == Status.AMBIGUOUS:
            return web.json_response({"ask": [{"id": w.id, "name": w.name} for w in result.candidates]}, status=409)
        if not result.ok:
            raise _result_error(result)
        await module.dm_goals(_UserShim(user), result, in_sprint=False)
        return web.json_response({"message": confirmation(PLAIN, result.changes, result.outcome)})

    async def stats(_request: web.Request, user: WebUser) -> web.Response:
        pid = await person(user)
        h = await history(tbot.storage, pid)
        today = bot_day(tbot.now())
        days = [today - timedelta(days=i) for i in range(29, -1, -1)]
        active = h.active_days()
        series = [
            {"day": d.isoformat(), "words": h.words(d, d), "active": d in active, "fillable": fillable(d, today)}
            for d in days
        ]
        month = today.replace(day=1)
        work_rows = []
        for w in await works.for_person(pid):
            row = work_json(w)
            row["month"] = h.words(month, today, w.id) if w.unit == Unit.WORDS else None
            if w.goal:
                row["goal_done"] = h.work_period(w, today, w.goal.period)
            work_rows.append(row)
        return web.json_response(
            {
                "today": h.words(today, today),
                "week": h.words(today - timedelta(days=today.weekday()), today),
                "month": h.words(month, today),
                "year": h.words(today.replace(month=1, day=1), today),
                "streak": h.streak(today),
                "sprints": len(h.completed()),
                "minutes": h.minutes(),
                "days": series,
                "works": work_rows,
            }
        )

    app.add("GET", "/api/sprint", get_sprint)
    app.add("POST", "/api/sprint/{sprint:\\d+}/join", join)
    app.add("POST", "/api/sprint/{sprint:\\d+}/toggle", toggle)
    app.add("POST", "/api/sprint/{sprint:\\d+}/report", report)
    app.add("POST", "/api/sprint/{sprint:\\d+}/outcome", outcome)
    app.add("GET", "/api/works", list_works)
    app.add("POST", "/api/works", add_work)
    app.add("PATCH", "/api/works/{work:\\d+}", update_work)
    app.add("DELETE", "/api/works/{work:\\d+}", delete_work)
    app.add("POST", "/api/privacy", set_privacy)
    app.add("POST", "/api/progress", progress)
    app.add("GET", "/api/stats", stats)


class _UserShim:
    """Enough of a Telegram user for :meth:`Module.dm_goals`."""

    def __init__(self, user: WebUser):
        self.id = int(user.user_id)


def _result_error(result) -> ApiError:
    if result.status == Status.LINK_ERROR:
        return ApiError(400, "common.link_error", error=result.message or "")
    return ApiError(400, _error_key(result.status), name=result.hint or "")


def _error_key(status: Status) -> str:
    return {
        Status.NOT_JOINED: "report.not_joined",
        Status.CLOSED: "report.closed",
        Status.NOT_STARTED: "report.not_started",
        Status.UNKNOWN_WORK: "report.unknown_work",
        Status.BAD_PERCENT: "report.bad_percent",
        Status.PERCENT_OFF: "report.percent_off",
        Status.TOO_MANY: "report.too_many",
        Status.LINK_ERROR: "report.nothing",
        Status.LINK_UNKNOWN: "report.link_unknown",
        Status.TOO_OLD: "progress.too_old",
        Status.NO_PERCENT_SPRINT: "progress.no_percent_sprint",
        Status.AMBIGUOUS: "discord.ambiguous",
    }.get(status, "report.nothing")
