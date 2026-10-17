from __future__ import annotations

from app.extensions import db
from app.models.complaint import Complaint
from app.models.order import Order
from app.models.photo import Photo


class DataQualityService:
    @staticmethod
    def run() -> dict:
        findings: list[dict[str, str]] = []
        counts: dict[str, int] = {}

        # 1. Дубли заказов: одинаковые (user_id, order_number)
        dupes = (
            db.session.query(Order.user_id, Order.order_number, db.func.count(Order.id))
            .filter(Order.order_number.isnot(None), Order.order_number != "")
            .group_by(Order.user_id, Order.order_number)
            .having(db.func.count(Order.id) > 1)
            .all()
        )
        counts["duplicate_orders"] = len(dupes)
        for u, num, c in dupes:
            findings.append(
                {"category": "Дубли", "object": f"Заказ {num}", "reason": f"Дубль: {c} записей у одного пользователя", "data": f"user_id={u}"}
            )

        # 2. Пропущенные значения в заказах
        missing_number = db.session.query(Order).filter((Order.order_number.is_(None)) | (Order.order_number == "")).count()
        counts["missing_order_number"] = missing_number
        if missing_number:
            findings.append({"category": "Пропуски", "object": "Заказы", "reason": "Отсутствует номер заказа", "data": f"количество: {missing_number}"})

        missing_courier = db.session.query(Order).filter((Order.courier_fio.is_(None)) | (Order.courier_fio == "")).count()
        counts["missing_courier"] = missing_courier
        if missing_courier:
            findings.append({"category": "Пропуски", "object": "Заказы", "reason": "Отсутствует курьер", "data": f"количество: {missing_courier}"})

        # Жалобы без текста
        missing_ct = db.session.query(Complaint).filter((Complaint.text.is_(None)) | (Complaint.text == "")).count()
        counts["missing_complaint_text"] = missing_ct
        if missing_ct:
            findings.append({"category": "Пропуски", "object": "Жалобы", "reason": "Пустой текст жалобы", "data": f"количество: {missing_ct}"})

        # 3. Невозможные временные интервалы
        bad_wait = 0
        for o in db.session.query(Order).filter(Order.wait_time.isnot(None), Order.wait_time != "").all():
            v = (o.wait_time or "").strip()
            try:
                # поддерживаем форматы "X ч Y мин" или числа (минуты)
                # попытаемся извлечь первое число
                nums = [float(x) for x in v.replace(",", ".").split() if _is_number(x)]
                if nums and (nums[0] < 0 or nums[0] > 24 * 60):
                    bad_wait += 1
                    findings.append({"category": "Интервалы", "object": f"Заказ {o.id}", "reason": "Невозможное время ожидания", "data": v})
            except Exception:
                bad_wait += 1
        counts["bad_wait_intervals"] = bad_wait

        # 4. Несогласованные связи (вычисляются ниже)
        pass
        # manual
        order_ids = {r[0] for r in db.session.query(Order.id).all()}
        orphan_c = [c for c in db.session.query(Complaint).all() if c.order_id not in order_ids]
        counts["orphan_complaints"] = len(orphan_c)
        for c in orphan_c[:50]:
            findings.append({"category": "Связи", "object": f"Жалоба {c.id}", "reason": "Complaint ссылается на несуществующий заказ", "data": f"order_id={c.order_id}"})

        orphan_photos = [p for p in db.session.query(Photo).all() if p.order_id not in order_ids]
        counts["orphan_photos"] = len(orphan_photos)
        for p in orphan_photos[:50]:
            findings.append({"category": "Связи", "object": f"Фото {p.id}", "reason": "Photo ссылается на несуществующий заказ", "data": f"order_id={p.order_id}"})

        # Расхождение зеркала Complaint и Order.complaint_text по количеству
        mismatched = 0
        for o in db.session.query(Order).all():
            mirror_count = len((o.complaint_text or "").split(";")) if (o.complaint_text or "").strip() else 0
            real_count = len(o.complaints or [])
            if mirror_count != real_count and ((o.complaint_text or "").strip() or real_count):
                mismatched += 1
                if mismatched <= 50:
                    findings.append({"category": "Расхождения", "object": f"Заказ {o.id}", "reason": "Расхождение зеркала и строк жалоб", "data": f"зеркало={mirror_count}, строк={real_count}"})
        counts["mirror_mismatch"] = mismatched

        return {"counts": counts, "findings": findings}


def _is_number(token: str) -> bool:
    try:
        float(token)
        return True
    except ValueError:
        return False
