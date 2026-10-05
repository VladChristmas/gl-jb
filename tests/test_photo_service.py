import io

from app.services.photo_service import PhotoService


class TestPhotoService:
    def test_allowed_file(self):
        assert PhotoService.allowed_file("test.jpg") is True
        assert PhotoService.allowed_file("test.jpeg") is True
        assert PhotoService.allowed_file("test.png") is True
        assert PhotoService.allowed_file("test.webp") is True
        assert PhotoService.allowed_file("test.gif") is False
        assert PhotoService.allowed_file("test.txt") is False
        assert PhotoService.allowed_file("test") is False

    def test_validate_file_success(self, sample_order):
        # Create a mock file with valid JPEG header
        # Minimal JPEG header: FF D8 FF E0 00 10 4A 46 49 46 00 01
        jpeg_header = bytes(
            [0xFF, 0xD8, 0xFF, 0xE0, 0x00, 0x10, 0x4A, 0x46, 0x49, 0x46, 0x00, 0x01]
        )
        file = io.BytesIO(jpeg_header + b"fake image content")
        file.filename = "test.jpg"

        is_valid, error = PhotoService.validate_file(file)
        assert is_valid is True
        assert error == ""

    def test_validate_file_no_filename(self):
        file = io.BytesIO(b"content")
        file.filename = ""

        is_valid, error = PhotoService.validate_file(file)
        assert is_valid is False
        assert "не выбран" in error.lower()

    def test_validate_file_invalid_extension(self):
        file = io.BytesIO(b"content")
        file.filename = "test.txt"

        is_valid, error = PhotoService.validate_file(file)
        assert is_valid is False
        assert "недопустимый формат" in error.lower()

    def test_validate_file_empty(self):
        file = io.BytesIO(b"")
        file.filename = "test.jpg"

        is_valid, error = PhotoService.validate_file(file)
        assert is_valid is False
        assert "пустой" in error.lower()

    def test_save_photo_invalid_file(self, db_session, sample_order, regular_user):
        file = io.BytesIO(b"")
        file.filename = "test.txt"

        photo = PhotoService.save_photo(file, sample_order.id, regular_user.id)
        assert photo is None
