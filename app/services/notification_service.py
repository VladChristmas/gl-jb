"""Notification service for manager dashboard.

Computed notifications from existing data — no new tables, no background jobs.
"""

from datetime import datetime
from typing import Any

from app.services.complaint_service import ComplaintService
from app.services.dashboard_service import DashboardService
from app.services.rating_service import RatingService


class NotificationService:
    """Build notification list for manager dashboard."""

    # Thresholds (can be moved to config later)
    COMPLAINTS_SPIKE_THRESHOLD = 10
    DEDUP_TTL_HOURS = 24

    @staticmethod
    def get_notifications(limit: int = 20) -> list[dict[str, Any]]:
        """Return computed notifications, sorted by severity then time (newest first)."""
        notifications = []

        # 1. Orders awaiting required photos
        pending_count = DashboardService.pending_orders_count()
        if pending_count > 0:
            notifications.append(
                NotificationService._make_notification(
                    ntype="pending_photos",
                    title="Заказы ждут фото-подтверждения",
                    severity="high",
                    description=f"{pending_count} заказ(ов) с обязательной фото-подтемой не имеют загруженных фото",
                    target_url="/admin/photos?problem=required_missing",
                    target_label="Открыть список",
                    entity_id="global",
                    count=pending_count,
                )
            )

        # 2. Complaints spike (weekly)
        complaints_spike = NotificationService._check_complaints_spike()
        if complaints_spike:
            notifications.append(complaints_spike)

        # 3. Courier quality drop
        quality_drop = NotificationService._check_courier_quality_drop()
        if quality_drop:
            notifications.extend(quality_drop)

        # 4. Mirror-only complaints (legacy data not hydrated)
        mirror_count = ComplaintService.mirror_only_count()
        if mirror_count > 0:
            notifications.append(
                NotificationService._make_notification(
                    ntype="mirror_only_complaints",
                    title="Необработанные жалобы в зеркале",
                    severity="low",
                    description=f"{mirror_count} заказ(ов) имеют жалобы только в старом поле complaint_text",
                    target_url="/admin/complaints",
                    target_label="Открыть жалобы",
                    entity_id="global",
                    count=mirror_count,
                )
            )

        # Sort: high > medium > low, then by time desc
        severity_order = {"high": 0, "medium": 1, "low": 2}
        notifications.sort(key=lambda n: (severity_order.get(n["severity"], 3), -n["timestamp"]))

        return notifications[:limit]

    @staticmethod
    def _check_complaints_spike() -> dict[str, Any] | None:
        """Check if complaints this week exceed threshold."""
        from app.services.dashboard_service import AnalyticsService

        # Get complaints for current week
        trends = AnalyticsService.complaint_trends(period="week")
        total_complaints = trends.get("total_complaints", 0)

        if total_complaints >= NotificationService.COMPLAINTS_SPIKE_THRESHOLD:
            return NotificationService._make_notification(
                ntype="complaints_spike",
                title="Рост количества жалоб",
                severity="high",
                description=f"За неделю зарегистрировано {total_complaints} жалоб (порог: {NotificationService.COMPLAINTS_SPIKE_THRESHOLD})",
                target_url="/admin/complaints?period=week",
                target_label="Открыть жалобы",
                entity_id="weekly",
                count=total_complaints,
            )
        return None

    @staticmethod
    def _check_courier_quality_drop() -> list[dict[str, Any]]:
        """Find couriers whose quality score worsened this week."""
        notifications = []
        try:
            report = RatingService.week_report()
            for row in report.get("rows", []):
                if row.get("direction") == "Ухудшение":
                    notifications.append(
                        NotificationService._make_notification(
                            ntype="courier_quality_drop",
                            title=f"Ухудшение качества: {row['fio']}",
                            severity="medium",
                            description=(
                                f"Балл упал с {row['prev']['quality_score']:.1f} до {row['quality_score']:.1f} "
                                f"(жалобы: {row['prev']['orders_with_complaints']} → {row['orders_with_complaints']})"
                            ),
                            target_url="/admin/couriers",
                            target_label="Открыть рейтинг",
                            entity_id=row["fio"],
                            count=1,
                        )
                    )
        except Exception:
            # Rating service may fail if no data — silently skip
            pass
        return notifications

    @staticmethod
    def _make_notification(
        ntype: str,
        title: str,
        severity: str,
        description: str,
        target_url: str,
        target_label: str,
        entity_id: str,
        count: int,
    ) -> dict[str, Any]:
        """Create notification dict with dedup key and timestamp."""
        now = datetime.utcnow()
        # Daily bucket for dedup
        date_bucket = now.strftime("%Y-%m-%d")
        dedup_key = f"{ntype}:{entity_id}:{date_bucket}"

        return {
            "type": ntype,
            "title": title,
            "severity": severity,
            "description": description,
            "target_url": target_url,
            "target_label": target_label,
            "entity_id": entity_id,
            "count": count,
            "timestamp": int(now.timestamp()),
            "time_iso": now.isoformat(),
            "dedup_key": dedup_key,
        }

    @staticmethod
    def filter_seen(notifications: list[dict], seen: dict[str, int]) -> list[dict]:
        """Filter out notifications already seen within TTL."""
        now = int(datetime.utcnow().timestamp())
        ttl_seconds = NotificationService.DEDUP_TTL_HOURS * 3600
        result = []
        for n in notifications:
            key = n["dedup_key"]
            last_seen = seen.get(key, 0)
            if now - last_seen >= ttl_seconds:
                result.append(n)
        return result
