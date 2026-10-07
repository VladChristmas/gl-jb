import pytest

from app.models import Message
from app.services.message_service import MessageService


@pytest.fixture(autouse=True)
def clean_messages(db_session):
    Message.query.delete()
    db_session.commit()
    yield


class TestChatAccess:
    def test_login_required(self, client):
        resp = client.get("/chat")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]

    def test_user_sees_messages_readonly(self, auth_client, db_session):
        MessageService.create_message("Важное объявление для всех")

        resp = auth_client.get("/chat")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert "Информационный чат" in html
        assert "Важное объявление для всех" in html
        # У обычного пользователя нет формы публикации и кнопки удаления
        assert 'name="text"' not in html
        assert "Опубликовать" not in html
        assert "/chat/delete/" not in html

    def test_admin_sees_publish_form(self, admin_client, db_session):
        resp = admin_client.get("/chat")
        html = resp.get_data(as_text=True)
        assert resp.status_code == 200
        assert 'name="text"' in html
        assert "Опубликовать" in html

    def test_empty_chat_shows_empty_state(self, auth_client):
        resp = auth_client.get("/chat")
        assert resp.status_code == 200
        assert "Сообщений пока нет" in resp.get_data(as_text=True)


class TestChatPosting:
    def test_admin_publishes_message(self, admin_client, db_session):
        resp = admin_client.post("/chat", data={"text": "Завтра смена с 8:00"})
        assert resp.status_code == 302
        assert Message.query.count() == 1

        resp = admin_client.get("/chat")
        assert "Завтра смена с 8:00" in resp.get_data(as_text=True)

    def test_user_cannot_publish(self, auth_client, db_session):
        resp = auth_client.post("/chat", data={"text": "Спам от курьера"})
        assert resp.status_code == 302
        assert Message.query.count() == 0

    def test_anonymous_cannot_publish(self, client, db_session):
        client.post("/chat", data={"text": "Аноним"})
        assert Message.query.count() == 0

    def test_empty_message_rejected(self, admin_client, db_session):
        admin_client.post("/chat", data={"text": "   "})
        assert Message.query.count() == 0

    def test_message_order_newest_first(self, admin_client, db_session):
        admin_client.post("/chat", data={"text": "Первое сообщение"})
        admin_client.post("/chat", data={"text": "Последнее сообщение"})

        resp = admin_client.get("/chat")
        html = resp.data.decode()
        assert html.index("Последнее сообщение") < html.index("Первое сообщение")


class TestChatDelete:
    def test_admin_deletes_message(self, admin_client, db_session):
        message = MessageService.create_message("Устаревшее сообщение")
        assert message is not None

        resp = admin_client.post(f"/chat/delete/{message.id}")
        assert resp.status_code == 302
        assert Message.query.count() == 0

    def test_user_cannot_delete(self, auth_client, db_session):
        message = MessageService.create_message("Оставить сообщение")
        assert message is not None

        auth_client.post(f"/chat/delete/{message.id}")
        assert Message.query.count() == 1

    def test_delete_unknown_message(self, admin_client, db_session):
        resp = admin_client.post("/chat/delete/99999")
        assert resp.status_code == 302
