# Домофон Интерсвязь для Home Assistant

![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)

![Домофон Интерсвязь](https://github.com/user-attachments/assets/0be9d430-7642-4955-8f43-d44bd06aa38b)

Интеграция Home Assistant для работы с домофонами **Интерсвязь**.

## Возможности

- Просмотр камер подъездов
- Открытие двери прямо из Home Assistant
- Авторизация по номеру телефона
- Авторизация по логину и паролю
- Поддержка нескольких адресов
- Поток через **go2rtc (MSE)** с минимальной задержкой

---

# Установка

## Через HACS (рекомендуется)

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hoolea&repository=intersvyaz_hass&category=integration)

После установки перезапустите Home Assistant.

## Ручная установка

1. Скачайте содержимое репозитория.
2. Скопируйте папку

```
custom_components/intersvyaz
```

в каталог

```
config/custom_components/
```

3. Перезапустите Home Assistant.

---

# Настройка

## 1. Установите дополнение go2rtc

### Через кнопку

[![Open your Home Assistant instance and show the add add-on repository dialog with a specific repository URL pre-filled.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FAlexxIT%2Fhassio-addons)

### Или вручную

**Настройки → Дополнения → Репозитории → Добавить**

```
https://github.com/AlexxIT/hassio-addons
```

После этого установите и запустите **go2rtc**.

---

## 2. Добавьте интеграцию

Перейдите в

**Настройки → Устройства и службы → Добавить интеграцию**

и выберите **Домофон Интерсвязь**.

Введите:

- номер телефона

или

- логин и пароль от личного кабинета Интерсвязь.

---

## 3. Настройте go2rtc

После авторизации интеграция автоматически отправляет конфигурацию потоков в **go2rtc** через HTTP API.

При необходимости можно изменить адрес сервера go2rtc в параметрах интеграции (**Интервал обновления**).

По умолчанию используется:

```
http://127.0.0.1:1984
```

После успешной настройки будут созданы:

- все камеры подъездов;
- кнопка **«Открыть домофон»**.

---

## 4. Добавьте камеры

Подключите RTSP-потоки (`rtsp://...`), созданные в go2rtc, через стандартную интеграцию Home Assistant **Generic Camera**.

https://www.home-assistant.io/integrations/generic/

---

# Примечания

- Требуется действующая учетная запись **Интерсвязь**.
- Для работы необходим доступ к Интернету.
- Интеграция использует официальный API:

```
https://api.is74.ru/auth/mobile
```

---

# Пример карточки Lovelace

![Карточка](https://github.com/user-attachments/assets/49a83747-7989-44e4-a481-a0d49bff3338)

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: camera.podjezd
    camera_image: camera.podjezd
    camera_view: live
    fit_mode: cover
    show_name: false
    show_state: false

  - type: entity-button
    entity: button.name
    name: Открыть дверь
    icon: mdi:door-open
    show_state: false
    show_icon: false
    tap_action:
      action: toggle
```

---

# Обнаружили проблему?

Если возникли ошибки или есть предложения по развитию проекта — создайте Issue:

https://github.com/hoolea/intersvyaz_hass/issues

---

# Отказ от ответственности

Данное программное обеспечение не связано с АО «Интерсвязь» и не является официальным продуктом компании.

Использование осуществляется **на свой страх и риск**. Автор не несет ответственности за возможный ущерб, причиненный использованием данного программного обеспечения, включая повреждение имущества или любой иной вред.

Все торговые марки и названия продуктов принадлежат их законным владельцам.

---

© 2026 **@hoolea**