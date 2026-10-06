from app.extensions import db


class Order(db.Model):  # type: ignore[name-defined]
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    order_number = db.Column(db.String(100))
    description = db.Column(db.Text)
    complaint_text = db.Column(db.Text)
    complaint_status = db.Column(db.String(50))
    complaint_date = db.Column(db.DateTime)
    external_id = db.Column(db.String(100), index=True)
    # Courier columns (Excel: Курьер, Адрес, Доставлено)
    courier_fio = db.Column(db.String(255))
    address = db.Column(db.String(500))
    delivered_at = db.Column(db.String(100))
    # Picker columns (Excel: Сборщик, Количество собранные заказы, Время ожидания, Скорость сборки)
    picker_fio = db.Column(db.String(255))
    pick_count = db.Column(db.Integer)
    wait_time = db.Column(db.String(100))
    pick_speed = db.Column(db.String(100))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    photos = db.relationship("Photo", backref="order", lazy=True, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Order {self.id}>"
