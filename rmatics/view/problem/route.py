from flask import Blueprint

from rmatics.utils.auth import require_judge_token
from rmatics.view.problem.problem import TrustedSubmitApi, ProblemApi, ProblemSubmissionsFilterApi
from rmatics.view.problem.run import SourceApi, UpdateRunFromEjudgeAPIv1, UpdateRunFromEjudgeAPIv2, ProtocolApi, RunAPI, RunStatusApi

problem_blueprint = Blueprint('problem', __name__, url_prefix='/problem')

problem_blueprint.add_url_rule('/trusted/<int:problem_id>/submit_v2', methods=('POST', ),
                               view_func=TrustedSubmitApi.as_view('trusted_submit'))

problem_blueprint.add_url_rule('/<int:problem_id>', methods=('GET', ),
                               view_func=ProblemApi.as_view('problem'))

problem_blueprint.add_url_rule('/<int:problem_id>/submissions/', methods=('GET', 'POST', ),
                               view_func=ProblemSubmissionsFilterApi.as_view('problem_submissions'))

problem_blueprint.add_url_rule('/run/<int:run_id>', methods=('PUT', ),
                               view_func=RunAPI.as_view('run'))

problem_blueprint.add_url_rule('/run/<int:run_id>/source', methods=('GET', ),
                               view_func=SourceApi.as_view('run_source'))

problem_blueprint.add_url_rule('/run/<int:run_id>/protocol', methods=('GET', ),
                               view_func=ProtocolApi.as_view('run_protocol'))

problem_blueprint.add_url_rule('/run/<int:run_id>/status', methods=('GET', ),
                               view_func=RunStatusApi.as_view('run_status'))

problem_blueprint.add_url_rule('/run/action/update_from_ejudge', methods=('POST', ),
                               view_func=UpdateRunFromEjudgeAPIv1.as_view('update_from_ejudge'))

problem_blueprint.add_url_rule('/run/action/update_from_ejudge_v2', methods=('POST', ),
                               view_func=require_judge_token(UpdateRunFromEjudgeAPIv2.as_view('update_from_ejudge_v2')))

problem_blueprint.add_url_rule('/run/<int:run_id>/action/rejudge', methods=('POST', ),
                               view_func=RunAPI.as_view('rejudge_run'))
