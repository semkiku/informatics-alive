from flask import Blueprint

from rmatics.view.judges.judges import JudgesApi

judges_blueprint = Blueprint('judges', __name__)

judges_blueprint.add_url_rule('/judges', methods=('GET', ),
                              view_func=JudgesApi.as_view('judges'))
