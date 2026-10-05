import secrets
import string

from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    fio = db.Column(db.String(255), unique=True, nullable=False)
    token = db.Column(db.String(10), unique=True, nullable=False)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    orders = db.relationship("Order", backref="user", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<User {self.fio}>"


class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    order_number = db.Column(db.String(100))
    description = db.Column(db.Text)
    complaint_text = db.Column(db.Text)  # Текст жалобы из горячей линии
    complaint_status = db.Column(db.String(50))  # Статус жалобы
    complaint_date = db.Column(db.DateTime)  # Дата жалобы
    external_id = db.Column(db.String(100), index=True)  # ID обращения из внешней системы
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    photos = db.relationship("Photo", backref="order", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Order {self.id}>"


class Photo(db.Model):
    __tablename__ = "photos"
    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(db.Integer, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False)
    filename = db.Column(db.String(255), nullable=False)
    original_filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())

    def __repr__(self):
        return f"<Photo {self.filename}>"


class ImportMapping(db.Model):
    __tablename__ = "import_mappings"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)  # "Заказы", "Жалобы"
    type = db.Column(db.String(20), nullable=False)  # 'orders', 'complaints'

    # Колонки для поиска/связи
    id_column = db.Column(db.String(100), nullable=False)  # колонка с ID обращения
    fio_column = db.Column(db.String(100))  # колонка с ФИО (только для заказов)
    order_number_column = db.Column(db.String(100))  # колонка с номером заказа

    # Дополнительные колонки как JSON: {"колонка_в_файле": "поле_в_модели"}
    field_mapping = db.Column(db.JSON, default={})

    # Настройки
    skip_first_row = db.Column(db.Boolean, default=True)  # пропускать заголовок
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def __repr__(self):
        return f"<ImportMapping {self.name} ({self.type})>"


def generate_token():
    alphabet = string.ascii_uppercase + string.digits
    while True:
        token = "".join(secrets.choice(alphabet) for _ in range(5))
        if not User.query.filter_by(token=token).first():
            return token
