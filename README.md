# Домофон Интерсвязь для Home Assistant

![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)

![407721900-12a28991-a136-4ef5-91eb-0014b9fd90de](https://github.com/user-attachments/assets/0be9d430-7642-4955-8f43-d44bd06aa38b)





## Описание
Эта интеграция позволяет управлять домофоном **Интерсвязь** напрямую из **Home Assistant**.

## Возможности
- Получение потоков камер со всего дома
- Открытие двери
- Поддержка авторизации через логин и пароль
- Поддержка авторизации по номеру телефона
- Выбор адреса

## Установка

### 1. Установка через HACS
[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=hoolea&repository=intersvyaz_hass&category=integration)

### 2. Ручная установка
1. Скачайте этот репозиторий
2. Переместите папку `custom_components/intersvyaz` в `config/custom_components/`
3. Перезапустите Home Assistant

## Настройка
1. Перейдите в **Настройки** → **Устройства и службы** → **Добавить интеграцию**
2. Найдите **Домофон Интерсвязь**
3. Введите ваш **номер телефона** либо **логин** и **пароль** от личного кабинета Интерсвязь 
4. После успешной авторизации появятся все **камеры с номерами подъездов** и  **кнопка "Открыть домофон"**

## Примечания
- Для работы необходим **доступ к интернету** и учетная запись Интерсвязи
- Интеграция использует API: `https://api.is74.ru/auth/mobile`
## Настройка карточки

![image](https://github.com/user-attachments/assets/49a83747-7989-44e4-a481-a0d49bff3338)

```yaml
type: vertical-stack
cards:
  - show_state: false
    show_name: false
    camera_view: live
    type: picture-entity
    entity: camera.is74_camera_6
  - show_name: true
    show_icon: false
    type: entity-button
    entity: button.otkryt_domofon
    name: Открыть дверь
    icon: mdi:door-open
    show_state: false
    tap_action:
      action: toggle
```

## Ошибки и предложения
Если у вас возникли проблемы или есть идеи для улучшения, создайте **issue** в [репозитории](https://github.com/USERNAME/intersvyaz_hass/issues).

---
## Отказ от ответственности
Данное программное обеспечение никак не связано и не одобрено АО «Интерсвязь». Используйте его на свой страх и риск. Автор ни при каких обстоятельствах не несёт ответственности за порчу или утрату вашего имущества и возможного вреда в отношении третьих лиц.

Все названия брендов и продуктов принадлежат их законным владельцам.

© 2025, @hoolea

