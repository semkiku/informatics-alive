"""Авторизация нотификаций о посылках по Bearer-токену.

Нотификации в update_from_ejudge_v2 приходят снаружи, поэтому notify-worker
присылает токен ejudge api того judge, от имени которого пришло сообщение
(тот же токен, что лежит в judges.json и с которым мы ходим в этот ejudge).
"""
import functools
from hmac import compare_digest
from typing import Optional

from flask import current_app, request
from werkzeug.exceptions import Forbidden, Unauthorized

from rmatics.ejudge.judges_config import get_judge


def _get_request_token() -> str:
    header = request.headers.get('Authorization', '')
    scheme, _, token = header.partition(' ')
    token = token.strip()
    if scheme.lower() != 'bearer' or not token:
        raise Unauthorized('Bearer token is required')
    return token


def _matches(token: str, expected: Optional[str]) -> bool:
    if not expected:
        return False
    # сравниваем байты: в заголовок может прийти что угодно,
    # а compare_digest не умеет строки с не-ASCII символами
    return compare_digest(token.encode('utf-8'), expected.encode('utf-8'))


def require_judge_token(view):
    """
    judge_id в теле нотификации обязателен: без него токен не с чем сверять.
    """
    @functools.wraps(view)
    def wrapper(*args, **kwargs):
        token = _get_request_token()
        data = request.get_json(force=True, silent=True) or {}

        try:
            judge_id = int(data.get('judge_id'))
        except (TypeError, ValueError):
            judge_id = None

        if judge_id is None:
            current_app.logger.error('Notification without judge_id')
            raise Forbidden('judge_id is required')

        judge = get_judge(judge_id)
        if judge is None or not _matches(token, judge.get_token()):
            raise Forbidden('Invalid token')
        return view(*args, **kwargs)
    return wrapper
