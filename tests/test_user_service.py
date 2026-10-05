import pytest
from app.services.user_service import UserService
from app.models import User


class TestUserService:
    def test_create_user(self, db_session):
        import uuid
        unique_fio = f'Test User {uuid.uuid4().hex[:8]}'
        user = UserService.create_user(unique_fio)
        assert user.fio == unique_fio
        assert user.token is not None
        assert len(user.token) == 5
        assert user.id is not None

    def test_get_user_by_id(self, db_session, admin_user):
        found = UserService.get_user_by_id(admin_user.id)
        assert found is not None
        assert found.fio == admin_user.fio

    def test_get_user_by_token(self, db_session, admin_user):
        found = UserService.get_user_by_token(admin_user.token)
        assert found is not None
        assert found.fio == admin_user.fio

    def test_get_user_by_token_not_found(self, db_session):
        found = UserService.get_user_by_token('INVALID')
        assert found is None

    def test_get_all_users(self, db_session, admin_user, regular_user):
        pagination = UserService.get_all_users(page=1, per_page=100)
        # Filter by the test users only
        test_users = [u for u in pagination.items if u.id in (admin_user.id, regular_user.id)]
        assert len(test_users) == 2

    def test_get_all_users_with_search(self, db_session, admin_user, regular_user):
        pagination = UserService.get_all_users(page=1, per_page=10, search=admin_user.fio[:8])
        found = [u for u in pagination.items if u.id == admin_user.id]
        assert len(found) == 1

    def test_delete_user(self, db_session, admin_user):
        user_id = admin_user.id
        user_fio = admin_user.fio
        deleted = UserService.delete_user(user_id)
        assert deleted is not None
        assert deleted.fio == user_fio
        
        # Verify user is deleted
        found = UserService.get_user_by_id(user_id)
        assert found is None

    def test_regenerate_token(self, db_session, admin_user):
        old_token = admin_user.token
        user = UserService.regenerate_token(admin_user.id)
        assert user is not None
        assert user.token != old_token
        assert len(user.token) == 5

    def test_get_users_map(self, db_session, admin_user, regular_user):
        users_map = UserService.get_users_map()
        admin_key = admin_user.fio.lower()
        regular_key = regular_user.fio.lower()
        assert admin_key in users_map
        assert regular_key in users_map
        assert users_map[admin_key] == admin_user.id
        assert users_map[regular_key] == regular_user.id