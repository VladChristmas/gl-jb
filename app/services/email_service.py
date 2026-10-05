from typing import cast

from flask import current_app

from app.models.order import Order
from app.models.user import User
from app.tasks import (
    send_email_task,
    send_order_completion_email_task,
    send_order_created_email_task,
)
from config import MAIL_ADMIN_RECIPIENTS, MAIL_PASSWORD, MAIL_USERNAME


class EmailService:
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
            # For simple emails without attachments, use async task
            if not attachments:
                send_email_task.delay(subject, recipients, html_body, text_body)
                return True

            # For emails with attachments, we still send synchronously
            # In the future, we could store attachments temporarily and pass paths
            from flask_mail import Message

            from app.extensions import mail
            from config import MAIL_DEFAULT_SENDER

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
        """Queue order completion email with photos as async task."""
        if not MAIL_ADMIN_RECIPIENTS:
            return False

        try:
            send_order_completion_email_task.delay(order.id, user.id)
            return True
        except Exception as e:
            current_app.logger.error(f"Failed to queue order completion email: {e}")
            return False

    @staticmethod
    def send_order_created_email(order: Order, user: User) -> bool:
        """Queue new order notification email as async task."""
        if not MAIL_ADMIN_RECIPIENTS:
            return False

        try:
            send_order_created_email_task.delay(order.id, user.id)
            return True
        except Exception as e:
            current_app.logger.error(f"Failed to queue order created email: {e}")
            return False
