import functools

from bson import ObjectId
from flask import request, current_app
from flask.views import MethodView
from marshmallow import fields, Schema, post_load
from pymongo.errors import PyMongoError, DuplicateKeyError
from webargs.flaskparser import parser
from werkzeug.exceptions import NotFound, BadRequest, InternalServerError
from sqlalchemy import or_, and_

from rmatics.ejudge.submit_queue.task import submit_task
from rmatics.model.base import db, mongo
from rmatics.model.rejudge import Rejudge
from rmatics.model.run import Run
from rmatics.tasks.notify import (
    NON_TERMINAL_STATUSES,
    _to_int,
    make_nonterminal_upd_chain,
    make_terminal_upd_chain,
)
from rmatics.utils.cacher.helpers import invalidate_monitor_cache_by_run
from rmatics.utils.response import jsonify
from rmatics.view.problem.serializers.run import RunSchema

POSSIBLE_SOURCE_ENCODINGS = ['utf-8', 'cp1251', 'windows-1251', 'ascii', 'koi8-r']


class FromEjudgeRunSchema(Schema):
    score = fields.Integer()
    status = fields.Integer()
    lang_id = fields.Integer()
    test_num = fields.Integer()
    create_time = fields.DateTime()
    last_change_time = fields.DateTime()

    @post_load
    def load_ejudge_update(self, data: dict):
        run = self.context.get('instance')
        for k, v in data.items():
            setattr(run, f'ejudge_{k}', v)

        return run


class RunAPI(MethodView):
    def put(self, run_id: int):
        """ View for updating run """
        data = request.get_json(force=True, silent=False)

        run = db.session.query(Run).get(run_id)
        if run is None:
            raise NotFound(f'Run with id #{run_id} is not found')

        only_fields = ['ejudge_status', 'ejudge_test_num', 'ejudge_score']
        load_run_schema = RunSchema(only=only_fields, context={'instance': run})
        _, errors = load_run_schema.load(data)

        if errors:
            raise BadRequest(errors)

        invalidate_monitor_cache_by_run(run)

        # Avoid excess DB queries
        excludes = ['user', 'problem']
        dump_run_schema = RunSchema(exclude=excludes)
        data, _ = dump_run_schema.dump(run)

        db.session.add(run)
        db.session.commit()

        return jsonify(data)

    def post(self, run_id: int):
        """ View for rejudge run"""
        run: Run = db.session.query(Run).get(run_id)
        if run is None:
            raise NotFound(f'Run with id #{run_id} is not found')

        rejudge = Rejudge(run_id=run.id,
                          ejudge_contest_id=run.ejudge_contest_id)
        db.session.add(rejudge)
        db.session.flush([rejudge])

        run.move_protocol_to_rejudge_collection(rejudge.id)

        run.ejudge_status = 377
        run.ejudge_test_num = None
        run.ejudge_score = None
        db.session.add(run)
        db.session.commit()

        submit_task.delay(run.id)

        return jsonify({})


class SourceApi(MethodView):

    get_args = {
        'is_admin': fields.Boolean(default=False, missing=False),
        'user_id': fields.Integer(),
        'context_source': fields.Integer(default=0),
    }

    def get(self, run_id: int):
        args = parser.parse(self.get_args, request)
        is_admin = args.get('is_admin')
        user_id = args.get('user_id')
        context_source = args.get('context_source')

        run_q = db.session.query(Run)

        if context_source and context_source > 0 and not is_admin:
            run_q = run_q.filter(or_(Run.context_source == context_source, Run.user_id == user_id))
        elif not is_admin:
            run_q = run_q.filter(Run.user_id == user_id)

        run = run_q.filter(Run.id == run_id).one_or_none()

        if run is None:
            raise NotFound(f'Run with id #{run_id} is not found')

        language_id = run.lang_id

        source = run.source or b''
        for encoding in POSSIBLE_SOURCE_ENCODINGS:
            try:
                source = source.decode(encoding)
                break
            except UnicodeDecodeError:
                pass

        return jsonify({'source': source, 'language_id': language_id})


class ProtocolApi(MethodView):

    get_args = {
        'is_admin': fields.Boolean(default=False, missing=False),
        'user_id': fields.Integer(),
        'context_source': fields.Integer(default=0),
    }

    def get(self, run_id: int):
        args = parser.parse(self.get_args, request)
        is_admin = args.get('is_admin')
        user_id = args.get('user_id')
        context_source = args.get('context_source')

        run_q = db.session.query(Run)

        if context_source and context_source > 0 and not is_admin:
            run_q = run_q.filter(or_(Run.context_source == context_source, Run.user_id == user_id))
        elif not is_admin:
            run_q = run_q.filter(Run.user_id == user_id)

        run = run_q.filter(Run.id == run_id).one_or_none()

        if run is None:
            raise NotFound(f'Run with id #{run_id} is not found')

        protocol = run.protocol
        if not protocol:
            raise NotFound(f'Protocol for run_id: {run_id} not found')

        return jsonify(protocol)


class RunStatusApi(MethodView):
    """Вердикт и итоговый балл посылки — без протокола."""

    get_args = {
        'is_admin': fields.Boolean(default=False, missing=False),
        'user_id': fields.Integer(),
        'context_source': fields.Integer(default=0),
    }

    def get(self, run_id: int):
        args = parser.parse(self.get_args, request)
        is_admin = args.get('is_admin')
        user_id = args.get('user_id')
        context_source = args.get('context_source')

        run_q = db.session.query(Run)

        if context_source and context_source > 0 and not is_admin:
            run_q = run_q.filter(or_(Run.context_source == context_source, Run.user_id == user_id))
        elif not is_admin:
            run_q = run_q.filter(Run.user_id == user_id)

        run = run_q.filter(Run.id == run_id).one_or_none()

        if run is None:
            raise NotFound(f'Run with id #{run_id} is not found')

        # ejudge_score = null, пока посылка не оттестирована
        return jsonify({'ejudge_status': run.ejudge_status,
                        'ejudge_score': run.ejudge_score})


class UpdateRunFromEjudgeAPIv1(MethodView):

    def post(self):
        data = request.get_json(force=True)
        ejudge_run_id = data['run_id']
        ejudge_contest_id = data['contest_id']
        mongo_protocol_id = data.get('mongo_protocol_id', None)

        run = db.session.query(Run) \
            .filter_by(ejudge_run_id=ejudge_run_id,
                       ejudge_contest_id=ejudge_contest_id) \
            .one_or_none()

        if run is None:
            msg = f'Cannot find Run with  \
                    ejudge_contest_id={ejudge_contest_id},  \
                    ejudge_run_id={ejudge_run_id}'
            raise BadRequest(msg)

        run_schema = FromEjudgeRunSchema(context={'instance': run})
        received_run, errors = run_schema.load(data)
        if errors:
            raise BadRequest(errors)

        if mongo_protocol_id:
            # If it is we should invalidate cache
            invalidate_monitor_cache_by_run(run)
            current_app.logger.debug('Cache invalidated')
            try:
                result = mongo.db.protocol.update_one({'_id': ObjectId(mongo_protocol_id)},
                                                      {'$set': {'run_id': received_run.id}})
                if not result.modified_count:
                    raise BadRequest(f'Cannot find protocol by _id {mongo_protocol_id}')
            except DuplicateKeyError:
                current_app.logger.exception('Found duplicate key for run_id')

            except PyMongoError:
                current_app.logger.exception('Looks like mongo is shutdown')
                raise InternalServerError(f'Looks like mongo is shutdown')

        db.session.add(received_run)
        db.session.commit()

        return jsonify({}, 200)

class UpdateRunFromEjudgeAPIv2(MethodView):

    def post(self):
        data = request.get_json(force=True)

        ejudge_run_id = _to_int(data.get('run_id'))
        ejudge_run_uuid = data.get('run_uuid')
        ejudge_contest_id = _to_int(data.get('contest_id'))
        status = _to_int(data.get('status'))
        judge_id = _to_int(data.get('judge_id'))

        if ejudge_run_id is None or ejudge_contest_id is None or ejudge_run_uuid is None or status is None or judge_id is None:
            msg = f'Incorrect data: {data}'
            raise BadRequest(msg)

        # Normalize numeric fields once so every downstream task gets ints
        # instead of the raw JSON strings the ejudge notification may send.
        data['run_id'] = ejudge_run_id
        data['contest_id'] = ejudge_contest_id
        data['status'] = status
        data['judge_id'] = judge_id
        if data.get('rmatics_run_id') is not None:
            data['rmatics_run_id'] = _to_int(data.get('rmatics_run_id'))

        if status in NON_TERMINAL_STATUSES:
            upd_chain = make_nonterminal_upd_chain()
        else:
            upd_chain = make_terminal_upd_chain()
            
        upd_chain.delay(data)

        return jsonify({}, 200)
