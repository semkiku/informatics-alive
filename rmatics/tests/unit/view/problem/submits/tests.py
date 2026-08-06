import datetime
import io
import json
from io import BytesIO
from unittest.mock import patch, MagicMock

from bson import ObjectId
from flask import url_for

from rmatics import monitor_cacher
from rmatics.model.base import db, mongo
from rmatics.model.run import Run
from rmatics.testutils import TestCase
from rmatics.utils.run import EjudgeStatuses
from rmatics.view.problem.problem import TrustedSubmitApi

PROTOCOL_ID = ObjectId("507f1f77bcf86cd799439011")
WRONG_PROTOCOL_ID = ObjectId("507f1f77bcf86cd799439012")


class TestCheckFileRestriction(TestCase):
    def setUp(self):
        super().setUp()

    def test_file_too_large(self):
        files = io.BytesIO(bytes((ascii('f') * 64 * 1024).encode('ascii')))
        with self.assertRaises(ValueError):
            TrustedSubmitApi.check_file_restriction(files)

        files = io.BytesIO(bytes((ascii('f') * 1).encode('ascii')))

        with self.assertRaises(ValueError):
            TrustedSubmitApi.check_file_restriction(files)


class TestTrustedProblemSubmit(TestCase):
    def setUp(self):
        super().setUp()
        self.create_ejudge_problems()
        self.create_users()
        self.create_statements()

    def send_request(self, problem_id, **kwargs):
        url = url_for('problem.trusted_submit', problem_id=problem_id)
        data = {
            'lang_id': 1,
            'statement_id': self.statements[0].id,
            'user_id': self.users[0].id,
            **kwargs
        }
        response = self.client.post(url, data=data, content_type='multipart/form-data')
        return response

    @patch('rmatics.view.problem.problem.Run.update_source')
    @patch('rmatics.view.problem.problem.submit_task')
    def test_simple(self, mock_submit_task, mock_update):
        file = BytesIO(b'skdjvndfkjnvfk')
        data = dict(
            file=(file, 'test.123', )
        )
        resp = self.send_request(self.ejudge_problems[0].id, **data)

        self.assert200(resp)
        mock_update.assert_called_once()
        mock_submit_task.delay.assert_called_once()

    @patch('rmatics.view.problem.problem.Run.update_source')
    @patch('rmatics.view.problem.problem.submit_task')
    def test_duplicate(self, mock_submit_task, mock_update):

        blob = b'skdjvndfkjnvfk'

        source_hash = Run.generate_source_hash(blob)

        run = Run(
            user_id=self.users[0].id,
            problem=self.ejudge_problems[0],
            problem_id=self.ejudge_problems[0].id,
            statement_id=self.statements[0].id,
            ejudge_contest_id=self.ejudge_problems[0].ejudge_contest_id,
            lang_id=1,
            ejudge_status=EjudgeStatuses.COMPILING.value,
            source_hash=source_hash,
        )
        db.session.add(run)
        db.session.commit()

        file = BytesIO(blob)
        data = dict(
            file=(file, 'test.123', )
        )
        resp = self.send_request(self.ejudge_problems[0].id, **data)

        self.assert400(resp)


class TestGetSubmissionSource(TestCase):
    def setUp(self):
        super().setUp()

        self.create_users()
        self.create_statements()
        self.create_ejudge_problems()

        blob = b'skdjvndfkjnvfk'

        source_hash = Run.generate_source_hash(blob)

        self.run = Run(
            user_id=self.users[0].id,
            problem=self.ejudge_problems[0],
            problem_id=self.ejudge_problems[0].id,
            statement_id=self.statements[0].id,
            ejudge_contest_id=self.ejudge_problems[0].ejudge_contest_id,
            lang_id=1,
            ejudge_status=EjudgeStatuses.COMPILING.value,
            source_hash=source_hash,
        )
        db.session.add(self.run)
        db.session.commit()

        self.run.update_source(blob)

    def send_request(self, run_id, data=None):
        data = data or {}
        url = url_for('problem.run_source', run_id=run_id, **data)
        response = self.client.get(url)
        return response

    def test_simple(self):
        data = {'user_id': self.users[0].id}

        resp = self.send_request(run_id=self.run.id, data=data)
        self.assert200(resp)

    def test_wrong_permissions(self):
        data = {'user_id': self.users[1].id}

        resp = self.send_request(run_id=self.run.id, data=data)
        self.assert404(resp)

    def test_super_permissions(self):
        data = {'is_admin': True}

        resp = self.send_request(run_id=self.run.id, data=data)
        self.assert200(resp)


class TestUpdateSubmissionFromEjudge(TestCase):
    def setUp(self):
        super().setUp()

        self.create_users()
        self.create_ejudge_problems()
        self.create_judges()

        self.monitor_invalidate_mock = MagicMock()

        monitor_cacher.invalidate_all_of = self.monitor_invalidate_mock

        blob = b'skdjvndfkjnvfk'

        source_hash = Run.generate_source_hash(blob)

        self.run = Run(
            user_id=self.users[0].id,
            problem=self.ejudge_problems[0],
            problem_id=self.ejudge_problems[0].id,
            statement_id=None,
            ejudge_contest_id=self.ejudge_problems[0].ejudge_contest_id,
            lang_id=1,
            ejudge_status=EjudgeStatuses.COMPILING.value,
            source_hash=source_hash,
            ejudge_run_id=1
        )
        db.session.add(self.run)
        db.session.commit()

    def send_request_to_update_run(self, **data):
        data = json.dumps(data)
        url = url_for('problem.update_from_ejudge_v2')
        resp = self.client.post(url, data=data, headers=self.judge_headers(1))
        return resp


    def test_terminal_status_runs_terminal_chain(self):
        with patch('rmatics.view.problem.run.make_terminal_upd_chain') as make_chain:
            chain = MagicMock()
            make_chain.return_value = chain
            request_data = {
                'run_id': self.run.ejudge_run_id,
                'contest_id': self.run.ejudge_contest_id,
                'run_uuid': 'uuid-1',
                'status': EjudgeStatuses.OK.value,
                'judge_id': 1,
            }
            resp = self.send_request_to_update_run(**request_data)

        self.assert200(resp)
        make_chain.assert_called_once()
        chain.delay.assert_called_once()

    def test_nonterminal_status_runs_nonterminal_chain(self):
        with patch('rmatics.view.problem.run.make_nonterminal_upd_chain') as make_chain:
            chain = MagicMock()
            make_chain.return_value = chain
            request_data = {
                'run_id': self.run.ejudge_run_id,
                'contest_id': self.run.ejudge_contest_id,
                'run_uuid': 'uuid-1',
                'status': EjudgeStatuses.RUNNING.value,
                'judge_id': 1,
            }
            resp = self.send_request_to_update_run(**request_data)

        self.assert200(resp)
        make_chain.assert_called_once()
        chain.delay.assert_called_once()

    def test_missing_required_fields_is_bad_request(self):
        # нет run_uuid (judge_id обязателен уже на уровне авторизации)
        resp = self.send_request_to_update_run(**{
            'run_id': self.run.ejudge_run_id,
            'contest_id': self.run.ejudge_contest_id,
            'status': EjudgeStatuses.OK.value,
            'judge_id': 1,
        })
        self.assert400(resp)

    def test_non_integer_status_is_bad_request(self):
        resp = self.send_request_to_update_run(**{
            'run_id': self.run.ejudge_run_id,
            'contest_id': self.run.ejudge_contest_id,
            'run_uuid': 'uuid-1',
            'status': 'not-an-int',
            'judge_id': 1,
        })
        self.assert400(resp)


class TestGetRunProtocol(TestCase):
    def setUp(self):
        super().setUp()

        self.create_users()
        self.create_ejudge_problems()

        blob = b'skdjvndfkjnvfk'

        source_hash = Run.generate_source_hash(blob)

        self.run1 = Run(
            user_id=self.users[0].id,
            problem=self.ejudge_problems[0],
            problem_id=self.ejudge_problems[0].id,
            statement_id=None,
            ejudge_contest_id=self.ejudge_problems[0].ejudge_contest_id,
            lang_id=1,
            ejudge_status=EjudgeStatuses.OK.value,
            source_hash=source_hash,
            ejudge_run_id=1
        )
        self.run2 = Run(
            user_id=self.users[1].id,
            problem=self.ejudge_problems[1],
            problem_id=self.ejudge_problems[1].id,
            statement_id=None,
            ejudge_contest_id=self.ejudge_problems[1].ejudge_contest_id,
            lang_id=1,
            ejudge_status=EjudgeStatuses.OK.value,
            source_hash=source_hash,
            ejudge_run_id=2
        )

        db.session.add(self.run1)
        db.session.commit()

    def send_request(self, run_id, data=None):
        data = data or {}
        url = url_for('problem.run_protocol', run_id=run_id, **data)
        response = self.client.get(url)
        return response

    def insert_protocol_to_mongo(self, run_id):
        protocol = {
            'run_id': run_id,
            'protocol': f'nice protocol about {run_id}',
        }
        mongo.db.protocol.insert_one(protocol)
        del protocol['_id']  # insert_one add _id field into inserted document
        # unmarshal_protocol дополняет протокол служебными полями
        return {**protocol, 'tests': {}, 'v': 2}

    def test_super_permissions_and_protocol_exist(self):
        source = self.insert_protocol_to_mongo(self.run1.id)
        data = {'is_admin': True}

        resp = self.send_request(run_id=self.run1.id, data=data)
        self.assert200(resp)
        self.assertEqual(resp.json['data'], source)

    def test_super_permissions_and_protocol_doesnt_exist(self):
        self.insert_protocol_to_mongo(self.run2.id)
        data = {'is_admin': True}

        resp = self.send_request(run_id=self.run1.id, data=data)
        self.assert404(resp)

    def test_student_have_own_protocol(self):
        source = self.insert_protocol_to_mongo(self.run1.id)
        data = {'is_admin': False, 'user_id': self.run1.user_id}

        resp = self.send_request(run_id=self.run1.id, data=data)
        self.assert200(resp)
        self.assertEqual(resp.json['data'], source)

    def test_student_doesnt_have_own_protocol(self):
        data = {'is_admin': False, 'user_id': self.run1.user_id}

        resp = self.send_request(run_id=self.run1.id, data=data)
        self.assert404(resp)

    def test_student_try_lookup_not_own_protocol(self):
        self.insert_protocol_to_mongo(self.run2.id)
        data = {'is_admin': False, 'user_id': self.run1.user_id}

        resp = self.send_request(run_id=self.run2.id, data=data)
        self.assert404(resp)
