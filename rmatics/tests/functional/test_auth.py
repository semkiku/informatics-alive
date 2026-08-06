from flask import url_for

from rmatics import db
from rmatics.model import Run
from rmatics.testutils import TestCase
from rmatics.utils.run import EjudgeStatuses


class TestJudgeTokenAuth(TestCase):
    """Ручка нотификаций закрыта токеном ejudge api, своим у каждого judge."""

    def setUp(self):
        super().setUp()

        self.create_users()
        self.create_ejudge_problems()
        self.create_judges()

        self.run = Run(
            user_id=self.users[0].id,
            problem_id=self.ejudge_problems[0].id,
            ejudge_contest_id=self.ejudge_problems[0].ejudge_contest_id,
            lang_id=1,
            ejudge_status=EjudgeStatuses.IN_QUEUE.value,
            ejudge_run_id=10,
            ejudge_run_uuid='uuid-10',
            judge_id=1,
        )
        db.session.add(self.run)
        db.session.commit()

    def send_notification(self, headers=None, **kwargs):
        data = {
            'run_id': 10,
            'contest_id': self.run.ejudge_contest_id,
            'run_uuid': 'uuid-10',
            'status': EjudgeStatuses.RUNNING.value,
            'judge_id': 1,
            'rmatics_run_id': self.run.id,
        }
        data.update(kwargs)
        url = url_for('problem.update_from_ejudge_v2')
        return self.client.post(url, json=data, headers=headers or {})

    def test_without_token_is_unauthorized(self):
        self.assert401(self.send_notification())

    def test_wrong_scheme_is_unauthorized(self):
        self.assert401(self.send_notification(
            headers={'Authorization': 'Token token-1'}))

    def test_own_judge_token_passes(self):
        self.assert200(self.send_notification(headers=self.judge_headers(1)))

    def test_another_judge_token_is_forbidden(self):
        """Токен второго ejudge не подходит к нотификации от первого."""
        self.assert403(self.send_notification(headers=self.judge_headers(2)))

    def test_wrong_token_is_forbidden(self):
        self.assert403(self.send_notification(
            headers={'Authorization': 'Bearer not-a-token'}))

    def test_unknown_judge_is_forbidden(self):
        self.assert403(self.send_notification(headers=self.judge_headers(1),
                                              judge_id=777))

    def test_without_judge_id_is_forbidden(self):
        """judge_id обязателен: без него токен не с чем сверять."""
        self.assert403(self.send_notification(headers=self.judge_headers(1),
                                              judge_id=None))
