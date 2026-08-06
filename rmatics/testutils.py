import flask
import flask_testing
import os
import unittest
import sys

# Локальный запуск без docker-compose: TEST_INFRA=local подменяет
# mysql/mongo/redis на sqlite/mongomock/fakeredis (см. rmatics/tests/lightweight.py)
if os.getenv('TEST_INFRA') == 'local':
    from rmatics.tests.lightweight import enable as _enable_lightweight_infra
    _enable_lightweight_infra()

from rmatics import create_app
from rmatics.model import CourseModule, Run
from rmatics.model.base import celery, db, mongo, redis
from rmatics.model.group import (
    Group,
    UserGroup,
)
from rmatics.model.problem import (
    EjudgeProblem,
    Problem)
from rmatics.model.statement import Statement, StatementProblem
from rmatics.model.user import SimpleUser

# В тестах celery-задачи выполняются синхронно, без брокера и воркеров.
# Настраиваем celery напрямую: CELERY_CONFIG из конфига приложения
# применяется только внутри create_app, а eager-режим нужен независимо
# от того, какое приложение создаётся.
celery.conf.task_always_eager = True
celery.conf.task_eager_propagates = True


def _unwrap_context_task():
    while celery.Task.__name__ == 'ContextTask':
        celery.Task = celery.Task.__mro__[1]


# При запуске через `flask test` CLI уже создал приложение из wsgi.py
# и celery.Task уже обёрнут — снимаем сразу при импорте.
_unwrap_context_task()

# Приложение одно на весь прогон: configure_celery_app при каждом create_app
# заново оборачивает celery.Task в ContextTask с новым app в замыкании,
# из-за чего задачи выполнялись бы в контексте самого первого приложения
# (не с теми judges/конфигом).
_app = None


def _get_test_app():
    global _app
    if _app is None:
        if flask.has_app_context():
            _app = flask.current_app._get_current_object()
        else:
            _app = create_app(config='rmatics.config.TestConfig')
            _unwrap_context_task()
        # Иначе при DEBUG=True упавший 500-кой запрос оставляет request
        # context на стеке и роняет весь прогон ("Popped wrong request context")
        _app.config['PRESERVE_CONTEXT_ON_EXCEPTION'] = False
    return _app


class TestCase(flask_testing.TestCase):
    CONFIG = {
        'SERVER_NAME': 'localhost',
        'URL_ENCODER_ALPHABET': 'abc',
    }

    def create_app(self):
        return _get_test_app()

    def setUp(self):
        # app общий на все тесты — судейский конфиг сбрасываем явно
        self.app.extensions['judges'] = {}
        self.app.config['DEFAULT_JUDGE_ID'] = None

        db.drop_all()
        db.create_all()

    def tearDown(self):
        db.session.remove()
        db.drop_all()

        assert mongo.db.name == 'test'
        mongo.db.client.drop_database(mongo.db)

        redis.flushdb()

    def judge_headers(self, judge_id: int) -> dict:
        """Заголовок, с которым notify-worker судьи judge_id присылает
        нотификации (токен ejudge api этого judge)."""
        return {'Authorization': f'Bearer {self.judges[judge_id].get_token()}'}

    def get_session(self):
        with self.client.session_transaction() as session:
            return dict(session)

    def set_session(self, data):
        with self.client.session_transaction() as session:
            session.update(data)

    def create_groups(self):
        self.groups = [
            Group(
                name='group 1',
                visible=1,
            ),
            Group(
                name='group 2',
                visible=1,
            ),
        ]
        db.session.add_all(self.groups)
        db.session.flush(self.groups)

    def create_ejudge_problems(self):
        self.ejudge_problems = [
            EjudgeProblem.create(
                ejudge_prid=1,
                contest_id=1,
                ejudge_contest_id=1,
                problem_id=1,
            ),
            EjudgeProblem.create(
                ejudge_prid=2,
                contest_id=2,
                ejudge_contest_id=1,
                problem_id=2,
            ),
            EjudgeProblem.create(
                ejudge_prid=3,
                contest_id=3,
                ejudge_contest_id=2,
                problem_id=1,
            )
        ]
        db.session.add_all(self.ejudge_problems)
        db.session.flush(self.ejudge_problems)

    def create_problems(self):
        self.problems = [
            Problem(name='Problem1', pr_id=self.ejudge_problems[0].id),
            Problem(name='Problem2', pr_id=self.ejudge_problems[1].id),
            Problem(name='Problem3', pr_id=self.ejudge_problems[2].id),
        ]
        db.session.add_all(self.problems)
        db.session.flush(self.problems)

    def create_statements(self):
        self.statements = [
            Statement(),
            Statement(),
        ]
        db.session.add_all(self.statements)
        db.session.flush(self.statements)

    def create_statement_problems(self):
        self.statement_problems = [
            StatementProblem(problem_id=self.problems[0].id,
                             statement_id=self.statements[0].id,
                             rank=1),
            StatementProblem(problem_id=self.problems[1].id,
                             statement_id=self.statements[0].id,
                             rank=1),
            StatementProblem(problem_id=self.problems[2].id,
                             statement_id=self.statements[0].id,
                             rank=1),
        ]
        db.session.add_all(self.statement_problems)
        db.session.flush(self.statement_problems)

    def create_course_module_statement(self):
        self.course_module_statement = CourseModule(instance_id=self.statements[0].id,
                                                    module=Statement.MODULE)
        db.session.add(self.course_module_statement)
        db.session.flush()

    def create_users(self):
        self.users = [
            SimpleUser(
                firstname='Maxim',
                lastname='Grishkin',
                ejudge_id=179,
            ),
            SimpleUser(
                firstname='Somebody',
                lastname='Oncetoldme',
                ejudge_id=1543,
            ),
            SimpleUser(
                firstname='Anotherone',
                lastname='Oncetoldme',
                ejudge_id=1544,
            ),
        ]
        db.session.add_all(self.users)
        db.session.flush(self.users)

    def create_judges(self):
        """Конфигурация judges (judges.json) + DEFAULT_JUDGE_ID для тестов."""
        from rmatics.ejudge.judges_config import JudgeConfig
        self.judges = {
            1: JudgeConfig(url='http://ejudge-1/cgi-bin/new-client',
                           name='new-ejudge', token='token-1'),
            2: JudgeConfig(url='http://ejudge-2/cgi-bin/new-client',
                           name='second-ejudge', token='token-2',
                           sender_user_id=7, lang_map={27: 62}),
        }
        self.app.extensions['judges'] = self.judges
        self.app.config['DEFAULT_JUDGE_ID'] = 1

    def create_runs(self):
        self.runs = [
            Run(problem_id=self.ejudge_problems[0].id,
                user_id=self.users[0].id),
            Run(problem_id=self.ejudge_problems[0].id,
                user_id=self.users[1].id),
            Run(problem_id=self.ejudge_problems[0].id,
                user_id=self.users[2].id),
        ]
        db.session.add_all(self.runs)


if __name__ == '__main__':
    if len(sys.argv) <= 1:
        path = 'tests'
    else:
        path = sys.argv[1]

    try:
        tests = unittest.TestLoader().discover(path)
    except:
        tests = unittest.TestLoader().loadTestsFromName(path)

    result = unittest.TextTestRunner(verbosity=2).run(tests).wasSuccessful()
    sys.exit(not result)
