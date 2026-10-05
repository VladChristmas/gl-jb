from typing import Optional, List
from app.models.user import User
from app.extensions import db
from app.utils.token import generate_token


class UserService:
    @staticmethod
    def create_user(fio: str) -> User:
        token = generate_token()
        user = User(fio=fio.strip(), token=token)
        db.session.add(user)
        db.session.commit()
        return user

    @staticmethod
    def get_user_by_id(user_id: int) -> Optional[User]:
        return User.query.get(user_id)

    @staticmethod
    def get_user_by_token(token: str) -> Optional[User]:
        return User.query.filter_by(token=token).first()

    @staticmethod
    def get_all_users(page: int = 1, per_page: int = 20, search: str = '') -> object:
        query = User.query
        if search:
            query = query.filter(User.fio.ilike(f'%{search}%'))
        return query.order_by(User.created_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

    @staticmethod
    def delete_user(user_id: int) -> Optional[User]:
        user = User.query.get(user_id)
        if user:
            db.session.delete(user)
            db.session.commit()
        return user

    @staticmethod
    def regenerate_token(user_id: int) -> Optional[User]:
        user = User.query.get(user_id)
        if user:
            user.token = generate_token()
            db.session.commit()
        return user

    @staticmethod
    def get_users_map() -> dict:
        return {u.fio.strip().lower(): u.id for u in User.query.all()}