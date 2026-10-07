import os
from typing import cast

from flask import current_app

from app.models.order import Order
from app.models.photo import Photo
from app.models.user import User
from config import (
    MAIL_ADMIN_RECIPIENTS,
    MAIL_DEFAULT_SENDER,
    MAIL_PASSWORD,
    MAIL_USERNAME,
    UPLOAD_FOLDER,
)


class EmailService:
    """Синхронная отправка писем.

    На проде нет Celery-воркера и Redis — постановка в очередь означала бы,
    что письмо никогда не уйдёт. Поэтому письмо собирается и отправляется
    сразу в том же запросе.
    """

    @staticmethod
    def is_configured() -> bool:
        """Почта настроена (логин, пароль и хотя бы один получатель-админ)."""
        return bool(MAIL_USERNAME and MAIL_PASSWORD and MAIL_ADMIN_RECIPIENTS)

    @staticmethod
    def send_email(
        subject: str,
        recipients: list[str],
        html_body: str,
        text_body: str | None = None,
        attachments: list[tuple] | None = None,
    ) -> bool:
        if not MAIL_USERNAME or not MAIL_PASSWORD:
            current_app.logger.warning("Email not configured, skipping send")
            return False

        try:
            from flask_mail import Message

            from app.extensions import mail

            msg = Message(
                subject=subject,
                recipients=cast("list[str | tuple[str, str]]", recipients),
                sender=MAIL_DEFAULT_SENDER,
                html=html_body,
                body=text_body or "",
            )
            if attachments:
                for filename, content, mimetype in attachments:
                    msg.attach(filename, mimetype, content)
            mail.send(msg)
            current_app.logger.info(f"Email sent to {recipients}: {subject}")
            return True
        except Exception as e:
            current_app.logger.error(f"Failed to send email: {e}")
            return False

    @staticmethod
    def send_order_completion_email(order: Order, user: User) -> bool:
        """Письмо админам о подтверждении заказа — с фото-вложениями."""
        if not EmailService.is_configured():
            current_app.logger.info("Email not configured, skipping completion email")
            return False

        try:
            from flask import render_template
            from flask_mail import Message

            from app.extensions import db, mail

            with db.session.no_autoflush:
                photos = Photo.query.filter_by(order_id=order.id).all()

                html = render_template(
                    "emails/order_completed.html", order=order, user=user, photos=photos
                )
                text = f"""
Заказ подтвержден: {order.order_number}
Клиент: {user.fio}
Описание: {order.description}
Фото: {len(photos)} шт.
Дата: {order.created_at}
"""

                msg = Message(
                    subject=f"✅ Заказ #{order.order_number} подтвержден — {len(photos)} фото",
                    recipients=cast("list[str | tuple[str, str]]", MAIL_ADMIN_RECIPIENTS),
                    sender=MAIL_DEFAULT_SENDER,
                    html=html,
                    body=text,
                )
                for photo in photos:
                    filepath = os.path.join(UPLOAD_FOLDER, photo.filename)
                    if os.path.exists(filepath):
                        with open(filepath, "rb") as f:
                            msg.attach(
                                photo.original_filename or photo.filename,
                                "image/jpeg",
                                f.read(),
                            )

                mail.send(msg)
                current_app.logger.info(f"Order completion email sent for order {order.id}")
                return True
        except Exception as e:
            current_app.logger.error(f"Failed to send completion email: {e}")
            return False

    @staticmethod
    def send_order_created_email(order: Order, user: User) -> bool:
        """Письмо админам о новом заказе."""
        if not EmailService.is_configured():
            current_app.logger.info("Email not configured, skipping created-order email")
            return False

        try:
            from flask import render_template
            from flask_mail import Message

            from app.extensions import mail

            html = render_template("emails/order_created.html", order=order, user=user)
            text = f"""
Новый заказ: {order.order_number}
Клиент: {user.fio}
Описание: {order.description}
Дата: {order.created_at}
"""
            msg = Message(
                subject=f"📦 Новый заказ #{order.order_number}",
                recipients=cast("list[str | tuple[str, str]]", MAIL_ADMIN_RECIPIENTS),
                sender=MAIL_DEFAULT_SENDER,
                html=html,
                body=text,
            )
            mail.send(msg)
            current_app.logger.info(f"Order created email sent for order {order.id}")
            return True
        except Exception as e:
            current_app.logger.error(f"Failed to send created-order email: {e}")
            return False
