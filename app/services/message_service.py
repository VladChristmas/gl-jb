from typing import cast

from app.extensions import db
from app.models.message import Message


class MessageService:
    """Информационный чат: публикуют только администраторы, читают все."""

    @staticmethod
    def get_messages(limit: int = 200) -> list[Message]:
        return cast(
            "list[Message]",
            Message.query.order_by(Message.created_at.desc(), Message.id.desc()).limit(limit).all(),
        )

    @staticmethod
    def create_message(text: str) -> Message | None:
        text = (text or "").strip()
        if not text:
            return None
        message = Message(text=text)
        db.session.add(message)
        db.session.commit()
        return message

    @staticmethod
    def delete_message(message_id: int) -> bool:
        message = db.session.get(Message, message_id)
        if not message:
            return False
        db.session.delete(message)
        db.session.commit()
        return True
