from celery import Celery
from flask import current_app
import os


def make_celery(app=None):
    """Create Celery instance, returns None if Redis not available."""
    broker = os.environ.get('CELERY_BROKER_URL')
    backend = os.environ.get('CELERY_RESULT_BACKEND')
    
    if not broker or not backend:
        return None
    
    try:
        celery = Celery(
            app.import_name if app else 'gl_jb',
            backend=backend,
            broker=broker,
        )

        celery.conf.update(
            task_serializer='json',
            accept_content=['json'],
            result_serializer='json',
            timezone='UTC',
            enable_utc=True,
            task_track_started=True,
            task_time_limit=30 * 60,
            worker_prefetch_multiplier=1,
            task_acks_late=True,
        )

        if app:
            celery.conf.update(app.config)

            class ContextTask(celery.Task):
                def __call__(self, *args, **kwargs):
                    with app.app_context():
                        return self.run(*args, **kwargs)

            celery.Task = ContextTask

        return celery
    except Exception:
        return None


# Create global instance (will be None if Redis not configured)
celery = make_celery()