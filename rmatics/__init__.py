from logging.config import dictConfig

from flask import Flask
from werkzeug.exceptions import HTTPException

from rmatics import cli
from rmatics.ejudge.judges_config import init_app as init_judges
from rmatics.model.base import db, celery
from rmatics.model.base import mongo
from rmatics.model.base import redis
from rmatics.plugins import monitor_cacher, invalidator
from rmatics.utils.centrifugo import centrifugo_client
from rmatics.view import handle_api_exception
from rmatics.view.judges.route import judges_blueprint
from rmatics.view.monitors.route import monitor_blueprint
from rmatics.view.problem.route import problem_blueprint


def create_app(config=None, config_logger=True):
    # Optional logger setup to prevent overriding
    # non-wsgi applications loggers
    if config_logger is True:
        init_logger()

    app = Flask(__name__)
    app.config.from_object(config)
    app.url_map.strict_slashes = False
    app.logger.info(f'Running with {config} module')

    db.init_app(app)
    mongo.init_app(app)
    configure_celery_app(app, celery)
    redis.init_app(app)
    init_judges(app)

    monitor_caching_time = app.config.get('MONITOR_CACHING_TIME_HOURS', 1) * 60 * 60
    monitor_cacher.init_app(app, redis, period=monitor_caching_time, autocommit=False)

    invalidator.init_app(remove_cache_func=redis.delete)

    # Centrifugo
    cent_url = app.config.get('CENTRIFUGO_URL')
    cent_api_key = app.config.get('CENTRIFUGO_API_KEY')
    centrifugo_client.init_app(cent_url, cent_api_key)

    app.register_error_handler(HTTPException, handle_api_exception)

    app.register_blueprint(problem_blueprint)
    app.register_blueprint(monitor_blueprint)
    app.register_blueprint(judges_blueprint)

    app.cli.add_command(cli.test)

    return app


def init_logger():
    dictConfig({
        'version': 1,
        'formatters': {'default': {
            'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
        }},
        'handlers': {'stdout': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://flask.logging.wsgi_errors_stream',
            'formatter': 'default'
        }},
        'root': {
            'level': 'INFO',
            'handlers': ['stdout']
        }
    })

def configure_celery_app(app, celery):
    """Configures the celery app.
    """
    celery.conf.update(app.config.get('CELERY_CONFIG', {}))
    TaskBase = celery.Task

    class ContextTask(TaskBase):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                try:
                    return TaskBase.__call__(self, *args, **kwargs)
                finally:
                    db.session.rollback()

    celery.Task = ContextTask


if __name__ == "__main__":
    app = create_app()
    app.run(debug=False)
