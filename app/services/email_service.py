import os
import threading
from typing import cast

from flask import Flask, current_app

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


def _send_completion_email_in_background(app: Flask, order_id: int, user_id: int) -> None:
    """Отправляет письмо в отдельном потоке со своим app-context."""
    try:
        with app.app_context():
            from app.extensions import db
            from app.models import Order as OrderModel
            from app.models import User as UserModel

            try:
                order = db.session.get(OrderModel, order_id)
                user = db.session.get(UserModel, user_id)
                if order is None or user is None:
                    app.logger.warning(
                        f"Completion email skipped: order {order_id} or user {user_id} "
                        "no longer exists"
                    )
                    return
                EmailService.send_order_completion_email(order, user)
            finally:
                db.session.remove()
    except Exception as exc:
        app.logger.error(f"Completion email failed in background: {exc}")


class EmailService:
    """Отправка писем.

    Основной путь — в фоне (`dispatch_order_completion_email`): SMTP в
    POST-запросе держал ответ до десятков секунд и блокировал загрузку фото.
    `send_*` остаётся синхронным для явных действий админа, где нужен
    немедленный ответ «отправлено/ошибка», и для Celery-тасков.
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
    def dispatch_order_completion_email(order: Order, user: User) -> None:
        """Ставит письмо о загруженном фото в фон и сразу возвращает управление.

        Приоритет: Celery (если настроен Redis) → daemon-поток с app-context.
        Ошибки отправки уходят только в лог: на результат загрузки фото
        письмо не влияет.
        """
        app = current_app._get_current_object()  # type: ignore[attr-defined]
        order_id, user_id = order.id, user.id

        if getattr(app, "celery", None) is not None:
            try:
                from app.tasks import send_order_completion_email_task

                send_order_completion_email_task.delay(order_id, user_id)
                app.logger.info(f"Completion email queued for order {order_id}")
                return
            except Exception as exc:
                app.logger.error(f"Failed to queue completion email, using thread: {exc}")

        threading.Thread(
            target=_send_completion_email_in_background,
            args=(app, order_id, user_id),
            daemon=True,
            name=f"completion-email-{order_id}",
        ).start()

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
