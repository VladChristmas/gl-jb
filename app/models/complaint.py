from datetime import datetime

from app.extensions import db


class Complaint(db.Model):  # type: ignore[name-defined]
    """Одна жалоба к заказу (заказ — много жалоб).

    Раньше `Order` хранил одну жалобу: `complaint_text` склеивался через `"; "`,
    а `complaint_status`/`complaint_date` перезаписывались каждой строкой импорта —
    статус и дата предыдущих жалоб терялись. Поля `Order.complaint_*` остались
    зеркалом последних жалоб, чтобы фильтры дашборда, admin_edit_order и шаблоны
    продолжали работать без изменений.
    """

    __tablename__ = "complaints"

    id = db.Column(db.Integer, primary_key=True)
    order_id = db.Column(
        db.Integer,
        db.ForeignKey("orders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    text = db.Column(db.Text, nullable=False)
    status = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    order = db.relationship("Order", back_populates="complaints")

    def __repr__(self):
        return f"<Complaint {self.id} order={self.order_id}>"
