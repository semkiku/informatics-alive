import mock
from flask import url_for
from mock import patch, MagicMock

from rmatics import db, mongo
from rmatics.model import Run
from rmatics.model.rejudge import Rejudge
from rmatics.testutils import TestCase
from rmatics.utils.run import EjudgeStatuses


class TestAPIUpdateRun(TestCase):
    def setUp(self):
        super().setUp()

        self.create_ejudge_problems()
        self.create_problems()
        self.create_users()

        self.run = Run(user_id=self.users[0].id, problem_id=self.problems[1].id,
                       ejudge_status=1, lang_id=1)
        db.session.add(self.run)
        db.session.commit()

    def send_request(self, run_id, data: dict):
        url = url_for('problem.run', run_id=run_id)
        resp = self.client.put(url, json=data)
        return resp

    def test_put_not_found_run(self):
        run_id = 777555
        resp = self.send_request(run_id, {})
        self.assert404(resp)

    def test_update_run(self):
        run_id = self.run.id
        with patch('rmatics.utils.cacher.helpers.monitor_cacher') as mc:
            mc.invalidate_all_of = MagicMock()
            self.monitor_invalidate_cache_mock = mc.invalidate_all_of
            resp = self.send_request(run_id, {'ejudge_status': '1488'})
        self.assert200(resp)

        run = db.session.query(Run).get(run_id)
        self.assertEqual(run.ejudge_status, 1488)
        self.monitor_invalidate_cache_mock.assert_called_once()


class TestRejudgeAPI(TestCase):
    def setUp(self):
        super().setUp()

        self.create_ejudge_problems()
        self.create_problems()
        self.create_users()

    def send_request(self, run_id):
        url = url_for('problem.rejudge_run', run_id=run_id)
        resp = self.client.post(url)
        return resp

    @mock.patch('rmatics.view.problem.run.submit_task')
    def test_simple(self, submit_task_mock):
        run = Run(user_id=self.users[0].id, problem_id=self.problems[1].id,
                  ejudge_status=EjudgeStatuses.WA.value, ejudge_score=40,
                  ejudge_test_num=5, lang_id=1, ejudge_contest_id=1)
        db.session.add(run)
        db.session.commit()

        protocol = {'my_protocol': 'data', 'run_id': run.id}
        mongo.db.protocol.insert_one(protocol)
        del protocol['_id']

        resp = self.send_request(run.id)

        self.assert200(resp)

        # посылка снова уходит в очередь (куда — решит submit_task)
        submit_task_mock.delay.assert_called_once_with(run.id)

        run = db.session.query(Run).get(run.id)
        self.assertEqual(run.ejudge_status, EjudgeStatuses.IN_QUEUE.value)
        self.assertIsNone(run.ejudge_score)
        self.assertIsNone(run.ejudge_test_num)

        rejudge = db.session.query(Rejudge) \
            .filter(Rejudge.run_id == run.id) \
            .filter(Rejudge.ejudge_contest_id == run.ejudge_contest_id) \
            .one()

        # старый протокол переехал в коллекцию rejudge...
        old_protocol = mongo.db.rejudge.find_one({'rjdgId': rejudge.id})
        self.assertIsNotNone(old_protocol)
        del old_protocol['_id']
        del old_protocol['rjdgId']
        self.assertEqual(old_protocol, protocol)

        # ...и в основной коллекции его больше нет
        self.assertIsNone(mongo.db.protocol.find_one({'run_id': run.id}))

    @mock.patch('rmatics.view.problem.run.submit_task')
    def test_rejudge_run_without_protocol(self, submit_task_mock):
        """Rejudge посылки без протокола (например, упавшей при отправке)."""
        run = Run(user_id=self.users[0].id, problem_id=self.problems[1].id,
                  ejudge_status=EjudgeStatuses.RMATICS_SUBMIT_ERROR.value,
                  lang_id=1, ejudge_contest_id=1)
        db.session.add(run)
        db.session.commit()

        resp = self.send_request(run.id)

        self.assert200(resp)
        submit_task_mock.delay.assert_called_once_with(run.id)

        rejudge = db.session.query(Rejudge) \
            .filter(Rejudge.run_id == run.id) \
            .one_or_none()
        self.assertIsNotNone(rejudge)

    @mock.patch('rmatics.view.problem.run.submit_task')
    def test_rejudge_not_found_run(self, submit_task_mock):
        resp = self.send_request(777555)
        self.assert404(resp)
        submit_task_mock.delay.assert_not_called()


class TestUpdateFromEjudgeE2E(TestCase):
    """Сквозной тест нотификации нового ejudge: POST
    /run/action/update_from_ejudge_v2 прогоняет celery-цепочку (eager)
    и обновляет Run + протокол."""

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

    def send_notification(self, **kwargs):
        data = {
            'run_id': 10,
            'contest_id': self.run.ejudge_contest_id,
            'run_uuid': 'uuid-10',
            'judge_id': 1,
            'rmatics_run_id': self.run.id,
        }
        data.update(kwargs)
        url = url_for('problem.update_from_ejudge_v2')
        return self.client.post(url, json=data, headers=self.judge_headers(1))

    @mock.patch('rmatics.tasks.notify.fetch_protocol')
    def test_terminal_notification_updates_run_and_protocol(self, fetch_mock):
        protocol = {'run_id': self.run.id, 'tests': {}, 'compiler_output': ''}
        fetch_mock.return_value = protocol

        resp = self.send_notification(status=EjudgeStatuses.OK.value,
                                      score=100, test_num=5)
        self.assert200(resp)

        run = db.session.query(Run).get(self.run.id)
        self.assertEqual(run.ejudge_status, EjudgeStatuses.OK.value)
        self.assertEqual(run.ejudge_score, 100)
        self.assertEqual(run.ejudge_test_num, 5)

        fetch_mock.assert_called_once()
        self.assertIsNotNone(
            mongo.db.protocol.find_one({'run_id': self.run.id}))

    @mock.patch('rmatics.tasks.notify.fetch_protocol')
    def test_nonterminal_notification_does_not_fetch_protocol(self, fetch_mock):
        resp = self.send_notification(status=EjudgeStatuses.RUNNING.value)
        self.assert200(resp)

        run = db.session.query(Run).get(self.run.id)
        self.assertEqual(run.ejudge_status, EjudgeStatuses.RUNNING.value)
        fetch_mock.assert_not_called()

    @mock.patch('rmatics.tasks.notify.fetch_protocol')
    def test_late_nonterminal_does_not_overwrite_terminal(self, fetch_mock):
        """Гонка нотификаций: RUNNING, пришедший после OK, игнорируется."""
        fetch_mock.return_value = {'run_id': self.run.id, 'tests': {}}

        self.send_notification(status=EjudgeStatuses.OK.value, score=100)
        self.send_notification(status=EjudgeStatuses.RUNNING.value)

        run = db.session.query(Run).get(self.run.id)
        self.assertEqual(run.ejudge_status, EjudgeStatuses.OK.value)
        self.assertEqual(run.ejudge_score, 100)

    def test_incomplete_notification_is_bad_request(self):
        url = url_for('problem.update_from_ejudge_v2')
        resp = self.client.post(url, json={'run_id': 10, 'status': 0, 'judge_id': 1},
                                headers=self.judge_headers(1))
        self.assert400(resp)


class TestUpdateFromEjudgeV1(TestCase):
    """Старая ручка POST /run/action/update_from_ejudge (нотификации
    старого ejudge-listener) оставлена параллельно с v2 для поэтапного
    деплоя — старый формат должен продолжать работать."""

    def setUp(self):
        super().setUp()

        self.create_users()
        self.create_ejudge_problems()

        self.run = Run(
            user_id=self.users[0].id,
            problem_id=self.ejudge_problems[0].id,
            ejudge_contest_id=self.ejudge_problems[0].ejudge_contest_id,
            lang_id=1,
            ejudge_status=EjudgeStatuses.COMPILING.value,
            ejudge_run_id=10,
        )
        db.session.add(self.run)
        db.session.commit()

    def send_notification(self, **kwargs):
        data = {
            'run_id': 10,
            'contest_id': self.run.ejudge_contest_id,
        }
        data.update(kwargs)
        url = url_for('problem.update_from_ejudge')
        return self.client.post(url, json=data)

    def test_updates_run_fields(self):
        resp = self.send_notification(status=EjudgeStatuses.OK.value,
                                      score=100, test_num=5)
        self.assert200(resp)

        run = db.session.query(Run).get(self.run.id)
        self.assertEqual(run.ejudge_status, EjudgeStatuses.OK.value)
        self.assertEqual(run.ejudge_score, 100)
        self.assertEqual(run.ejudge_test_num, 5)

    def test_binds_mongo_protocol_to_run(self):
        inserted = mongo.db.protocol.insert_one({'protocol': 'text'})

        resp = self.send_notification(
            status=EjudgeStatuses.OK.value,
            mongo_protocol_id=str(inserted.inserted_id))
        self.assert200(resp)

        protocol = mongo.db.protocol.find_one({'_id': inserted.inserted_id})
        self.assertEqual(protocol['run_id'], self.run.id)

    def test_unknown_run_is_bad_request(self):
        resp = self.send_notification(run_id=777555,
                                      status=EjudgeStatuses.OK.value)
        self.assert400(resp)
