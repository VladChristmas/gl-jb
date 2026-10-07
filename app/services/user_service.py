from flask_sqlalchemy.pagination import Pagination

from app.constants import ROLE_COURIER, USER_ROLES
from app.extensions import db
from app.models.user import User
from app.utils.token import generate_token


def _invalidate_stats():
    from app.services.order_service import OrderService

    OrderService.invalidate_stats_cache()


class UserService:
    @staticmethod
    def create_user(fio: str, role: str = ROLE_COURIER) -> User:
        if role not in USER_ROLES:
            role = ROLE_COURIER
        token = generate_token()
        user = User(fio=fio.strip(), token=token, role=role)
        db.session.add(user)
        db.session.commit()
        _invalidate_stats()
        return user

    @staticmethod
    def get_user_by_id(user_id: int) -> User | None:
        return db.session.get(User, user_id)

    @staticmethod
    def get_user_by_token(token: str) -> User | None:
        user: User | None = User.query.filter_by(token=token).first()
        return user

    @staticmethod
    def get_all_users(page: int = 1, per_page: int = 20, search: str = "") -> Pagination:
        query = User.query
        if search:
            query = query.filter(User.fio.ilike(f"%{search}%"))
        pagination: Pagination = query.order_by(User.created_at.desc(), User.id.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )
        return pagination

    @staticmethod
    def delete_user(user_id: int) -> User | None:
        user = db.session.get(User, user_id)
        if user:
            db.session.delete(user)
            db.session.commit()
            _invalidate_stats()
        return user

    @staticmethod
    def regenerate_token(user_id: int) -> User | None:
        user = db.session.get(User, user_id)
        if user:
            user.token = generate_token()
            db.session.commit()
        return user

    @staticmethod
    def set_role(user_id: int, role: str) -> User | None:
        user = db.session.get(User, user_id)
        if user and role in USER_ROLES:
            user.role = role
            db.session.commit()
        return user

    @staticmethod
    def get_users_map() -> dict:
        return {u.fio.strip().lower(): u.id for u in User.query.all()}
