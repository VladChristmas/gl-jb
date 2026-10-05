from app.extensions import db


class ImportMapping(db.Model):
    __tablename__ = 'import_mappings'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    type = db.Column(db.String(20), nullable=False)

    id_column = db.Column(db.String(100), nullable=False)
    fio_column = db.Column(db.String(100))
    order_number_column = db.Column(db.String(100))

    field_mapping = db.Column(db.JSON, default={})

    skip_first_row = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, server_default=db.func.now())
    updated_at = db.Column(db.DateTime, server_default=db.func.now(), onupdate=db.func.now())

    def __repr__(self):
        return f'<ImportMapping {self.name} ({self.type})>'