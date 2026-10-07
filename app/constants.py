# Жалобы, на которые курьер обязан прислать фото-подтверждение
PHOTO_REQUIRED_TOPICS = frozenset(
    {
        "Товар побит/вскрыт",
        "Не донесли часть товаров из заказа",
        "Не учли комментарий к заказу",
        "Принесли чужой заказ",
    }
)

# Роли пользователей
ROLE_ADMIN = "admin"
ROLE_COURIER = "courier"
ROLE_PICKER = "picker"
USER_ROLES = (ROLE_ADMIN, ROLE_COURIER, ROLE_PICKER)
ROLE_LABELS = {
    ROLE_ADMIN: "Администратор",
    ROLE_COURIER: "Курьер",
    ROLE_PICKER: "Сборщик",
}
