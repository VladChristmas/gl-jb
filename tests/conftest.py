import os
import uuid

import pytest

# Set test env BEFORE importing app/config (config.py loads .env at import time)
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["SECRET_KEY"] = "test-secret-key"
os.environ["ADMIN_PASSWORD"] = "test-admin-password"
os.environ["ADMIN_TOKEN"] = "TEST1"
os.environ["FLASK_DEBUG"] = "1"
os.environ["MAIL_USERNAME"] = ""
os.environ["MAIL_PASSWORD"] = ""

from app import create_app
from app.extensions import db
from app.models import Order, User


@pytest.fixture(scope="session")
def app():
    """Create application for testing."""
    app = create_app()
    app.config["TESTING"] = True
    app.config["WTF_CSRF_ENABLED"] = False

    with app.app_context():
        db.create_all()
        yield app
        db.drop_all()


@pytest.fixture(scope="function")
def client(app):
    """Create test client."""
    return app.test_client()


@pytest.fixture(scope="function")
def db_session(app):
    """Create database session for testing."""
    with app.app_context():
        yield db.session


@pytest.fixture(scope="function")
def admin_user(app, db_session):
    """Create admin user for testing."""
    unique_id = uuid.uuid4().hex[:8]
    token = f"ADM{unique_id}"
    user = User(fio=f"Admin User {unique_id}", token=token)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture(scope="function")
def regular_user(app, db_session):
    """Create regular user for testing."""
    unique_id = uuid.uuid4().hex[:8]
    token = f"USR{unique_id}"
    user = User(fio=f"Test User {unique_id}", token=token)
    db_session.add(user)
    db_session.commit()
    return user


@pytest.fixture(scope="function")
def sample_order(app, db_session, regular_user):
    """Create sample order for testing."""
    order = Order(
        user_id=regular_user.id, order_number="TEST-001", description="Test order description"
    )
    db_session.add(order)
    db_session.commit()
    return order


@pytest.fixture(scope="function")
def auth_client(client, regular_user):
    """Create authenticated client for regular user."""
    with client.session_transaction() as sess:
        sess["user_id"] = regular_user.id
        sess["user_fio"] = regular_user.fio
    return client


@pytest.fixture(scope="function")
def admin_client(client, admin_user):
    """Create authenticated client for admin."""
    with client.session_transaction() as sess:
        sess["is_admin"] = True
    return client
