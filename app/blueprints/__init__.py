from app.blueprints.auth import auth_bp
from app.blueprints.admin import admin_bp
from app.blueprints.user import user_bp
from app.blueprints.main import main_bp

__all__ = ['auth_bp', 'admin_bp', 'user_bp', 'main_bp']