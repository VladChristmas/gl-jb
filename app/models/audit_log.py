from app.extensions import db


class AuditLog(db.Model):  # type: ignore[name-defined]
    """Server-side audit trail for security-relevant actions.

    Records who did what, when, and (for updates) what changed.
    Immutable — no UPDATE/DELETE allowed for regular users.
    """

    __tablename__ = "audit_log"

    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now(), nullable=False, index=True)

    # Actor — nullable for system actions (e.g. import, background jobs)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    user_fio = db.Column(db.String(255))  # denormalized for display if user deleted

    # Action classification
    action_type = db.Column(db.String(30), nullable=False, index=True)  # create, update, delete, import, upload, role_change
    object_type = db.Column(db.String(30), nullable=False, index=True)  # order, complaint, photo, import, user
    object_id = db.Column(db.String(100), nullable=False, index=True)   # order_number, complaint_id, photo_id, import mapping id, user id

    # Human-readable summary (safe for logs, no PII/PD)
    description = db.Column(db.Text, nullable=False)

    # Structured diff for updates: {"field": {"old": ..., "new": ...}}
    diff_json = db.Column(db.JSON, nullable=True)

    # Optional context
    ip_address = db.Column(db.String(45), nullable=True)
    request_id = db.Column(db.String(64), nullable=True, index=True)

    # Relationship to user (for admin UI)
    user = db.relationship("User", backref=db.backref("audit_logs", lazy="dynamic"))

    def __repr__(self):
        return f"<AuditLog {self.id} {self.action_type} {self.object_type}:{self.object_id}>"
