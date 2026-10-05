from flask import Blueprint, send_from_directory, jsonify, redirect, url_for
from config import UPLOAD_FOLDER
from datetime import datetime


main_bp = Blueprint('main', __name__)


@main_bp.route('/')
def index():
    return redirect(url_for('auth.unified_login'))


@main_bp.route('/health')
def health_check():
    return jsonify({'status': 'ok', 'timestamp': datetime.utcnow().isoformat()})


@main_bp.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)