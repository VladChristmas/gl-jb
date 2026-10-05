from flask import Blueprint, send_from_directory, jsonify, redirect, url_for
from config import UPLOAD_FOLDER
from datetime import datetime


main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    return redirect(url_for('auth.unified_login'))


@main_bp.route('/health')
def health_check():
    from app.extensions import db
    from sqlalchemy import text

    payload = {'status': 'ok', 'timestamp': datetime.utcnow().isoformat()}
    try:
        db.session.execute(text('SELECT 1'))
        payload['database'] = 'ok'
    except Exception:
        db.session.rollback()
        payload['status'] = 'degraded'
        payload['database'] = 'error'
        return jsonify(payload), 503
    return jsonify(payload)


@main_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)