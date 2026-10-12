from app.extensions import db


class OperationalAnalysisSession(db.Model):  # type: ignore[name-defined]
    __tablename__ = "operational_analysis_sessions"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(255), nullable=False)
    uploaded_at = db.Column(db.DateTime, server_default=db.func.now())
    status = db.Column(db.String(50), default="processing")
    total_complaints = db.Column(db.Integer, default=0)
    total_orders = db.Column(db.Integer, default=0)
    total_assembly_orders = db.Column(db.Integer, default=0)
    matched_complaints = db.Column(db.Integer, default=0)
    matched_orders = db.Column(db.Integer, default=0)
    matched_assembly = db.Column(db.Integer, default=0)
    error_count = db.Column(db.Integer, default=0)
    warning_count = db.Column(db.Integer, default=0)
    wait_threshold_minutes = db.Column(db.Integer, default=30)

    def __repr__(self):
        return f"<OperationalAnalysisSession {self.id}: {self.filename}>"


class PickerMetrics(db.Model):  # type: ignore[name-defined]
    __tablename__ = "picker_metrics"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("operational_analysis_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    picker_fio = db.Column(db.String(255), nullable=False, index=True)
    unique_orders = db.Column(db.Integer, default=0)
    total_assembly_time_minutes = db.Column(db.Float, default=0.0)
    avg_assembly_time_minutes = db.Column(db.Float, default=0.0)
    median_assembly_time_minutes = db.Column(db.Float, default=0.0)
    speed_orders_per_minute = db.Column(db.Float, default=0.0)
    speed_items_per_minute = db.Column(db.Float, default=0.0)
    total_items = db.Column(db.Integer, default=0)
    avg_wait_time_minutes = db.Column(db.Float, default=0.0)
    median_wait_time_minutes = db.Column(db.Float, default=0.0)
    min_wait_time_minutes = db.Column(db.Float, default=0.0)
    max_wait_time_minutes = db.Column(db.Float, default=0.0)
    orders_above_wait_threshold = db.Column(db.Integer, default=0)
    orders_with_complaints = db.Column(db.Integer, default=0)
    total_complaints = db.Column(db.Integer, default=0)
    complaint_rate = db.Column(db.Float, default=0.0)
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    session = db.relationship(
        "OperationalAnalysisSession",
        backref=db.backref("picker_metrics", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<PickerMetrics {self.picker_fio}: {self.unique_orders} orders>"


class AssemblyOrder(db.Model):  # type: ignore[name-defined]
    __tablename__ = "assembly_orders"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("operational_analysis_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    order_id = db.Column(db.String(100), nullable=False, index=True)
    picker_fio = db.Column(db.String(255), nullable=False, index=True)
    assembly_start = db.Column(db.DateTime)
    assembly_end = db.Column(db.DateTime)
    assembly_duration_minutes = db.Column(db.Float)
    order_created_at = db.Column(db.DateTime)
    wait_time_minutes = db.Column(db.Float)
    items_count = db.Column(db.Integer)
    has_complaint = db.Column(db.Boolean, default=False)
    complaint_count = db.Column(db.Integer, default=0)
    match_status = db.Column(db.String(50))
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    session = db.relationship(
        "OperationalAnalysisSession",
        backref=db.backref("assembly_orders", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<AssemblyOrder {self.order_id}: {self.picker_fio}>"


class DataQualityIssue(db.Model):  # type: ignore[name-defined]
    __tablename__ = "data_quality_issues"
    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(
        db.Integer,
        db.ForeignKey("operational_analysis_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    issue_type = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text)
    source_sheet = db.Column(db.String(50))
    row_number = db.Column(db.Integer)
    order_id = db.Column(db.String(100))
    picker_fio = db.Column(db.String(255))
    severity = db.Column(db.String(20), default="warning")
    created_at = db.Column(db.DateTime, server_default=db.func.now())

    session = db.relationship(
        "OperationalAnalysisSession",
        backref=db.backref("quality_issues", lazy=True, cascade="all, delete-orphan"),
    )

    def __repr__(self):
        return f"<DataQualityIssue {self.issue_type}: {self.description[:50]}>"
