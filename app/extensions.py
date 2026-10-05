from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_migrate import Migrate
from flask_mail import Mail

db = SQLAlchemy()
csrf = CSRFProtect()
limiter = Limiter(get_remote_address)
migrate = Migrate()
mail = Mail()