from app.celery_app import celery
from flask import current_app
from flask_mail import Message
from app.extensions import mail
from config import MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER, MAIL_ADMIN_RECIPIENTS, UPLOAD_FOLDER
import os


# Only define tasks if celery is available
if celery is not None:
    @celery.task(bind=True, max_retries=3, default_retry_delay=60)
    def send_email_task(self, subject, recipients, html_body, text_body=None, attachments=None):
        """Async task to send email."""
        if not MAIL_USERNAME or not MAIL_PASSWORD:
            current_app.logger.warning('Email not configured, skipping send')
            return {'status': 'skipped', 'reason': 'email_not_configured'}

        try:
            msg = Message(
                subject=subject,
                recipients=recipients,
                sender=MAIL_DEFAULT_SENDER,
                html=html_body,
                body=text_body or ''
            )
            if attachments:
                for filename, content, mimetype in attachments:
                    msg.attach(filename, mimetype, content)
            mail.send(msg)
            current_app.logger.info(f'Email sent to {recipients}: {subject}')
            return {'status': 'sent', 'recipients': recipients}
        except Exception as exc:
            current_app.logger.error(f'Failed to send email: {exc}')
            raise self.retry(exc=exc)


    @celery.task(bind=True, max_retries=3, default_retry_delay=60)
    def send_order_completion_email_task(self, order_id, user_id):
        """Async task to send order completion email with photos."""
        from app.models import Order, User, Photo
        from app.extensions import db
        from flask import render_template

        if not MAIL_ADMIN_RECIPIENTS:
            return {'status': 'skipped', 'reason': 'no_admin_recipients'}

        try:
            with db.session.no_autoflush:
                order = db.session.get(Order, order_id)
                user = db.session.get(User, user_id)
                if not order or not user:
                    return {'status': 'error', 'reason': 'order_or_user_not_found'}

                photos = Photo.query.filter_by(order_id=order.id).all()

                html = render_template('emails/order_completed.html', order=order, user=user, photos=photos)
                text = f'''
Заказ подтвержден: {order.order_number}
Клиент: {user.fio}
Описание: {order.description}
Фото: {len(photos)} шт.
Дата: {order.created_at}
'''

                attachments = []
                for photo in photos:
                    filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
                    if os.path.exists(filepath):
                        with open(filepath, 'rb') as f:
                            attachments.append((
                                photo.original_filename or photo.filename,
                                f.read(),
                                'image/jpeg'
                            ))

                msg = Message(
                    subject=f'✅ Заказ #{order.order_number} подтвержден — {len(photos)} фото',
                    recipients=MAIL_ADMIN_RECIPIENTS,
                    sender=MAIL_DEFAULT_SENDER,
                    html=html,
                    body=text
                )
                if attachments:
                    for filename, content, mimetype in attachments:
                        msg.attach(filename, mimetype, content)

                mail.send(msg)
                current_app.logger.info(f'Order completion email sent for order {order_id}')
                return {'status': 'sent', 'order_id': order_id}

        except Exception as exc:
            current_app.logger.error(f'Failed to send order completion email: {exc}')
            raise self.retry(exc=exc)


    @celery.task(bind=True, max_retries=3, default_retry_delay=60)
    def send_order_created_email_task(self, order_id, user_id):
        """Async task to send new order notification email."""
        from app.models import Order, User
        from app.extensions import db
        from flask import render_template

        if not MAIL_ADMIN_RECIPIENTS:
            return {'status': 'skipped', 'reason': 'no_admin_recipients'}

        try:
            with db.session.no_autoflush:
                order = db.session.get(Order, order_id)
                user = db.session.get(User, user_id)
                if not order or not user:
                    return {'status': 'error', 'reason': 'order_or_user_not_found'}

                html = render_template('emails/order_created.html', order=order, user=user)
                text = f'''
Новый заказ: {order.order_number}
Клиент: {user.fio}
Описание: {order.description}
Дата: {order.created_at}
'''

                msg = Message(
                    subject=f'📦 Новый заказ #{order.order_number}',
                    recipients=MAIL_ADMIN_RECIPIENTS,
                    sender=MAIL_DEFAULT_SENDER,
                    html=html,
                    body=text
                )

                mail.send(msg)
                current_app.logger.info(f'Order created email sent for order {order_id}')
                return {'status': 'sent', 'order_id': order_id}

        except Exception as exc:
            current_app.logger.error(f'Failed to send order created email: {exc}')
            raise self.retry(exc=exc)

else:
    # Celery not available - define dummy functions for type checking
    def send_email_task(*args, **kwargs):
        return {'status': 'skipped', 'reason': 'celery_not_available'}

    def send_order_completion_email_task(*args, **kwargs):
        return {'status': 'skipped', 'reason': 'celery_not_available'}

    def send_order_created_email_task(*args, **kwargs):
        return {'status': 'skipped', 'reason': 'celery_not_available'}