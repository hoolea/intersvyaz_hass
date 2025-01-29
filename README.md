# Домофон Интерсвязь для Home Assistant

![HACS Badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=flat-square)

## Описание
Эта интеграция позволяет управлять домофоном **Интерсвязь** напрямую из **Home Assistant**.

## Возможности
- Получение потоков камер со всего дома
- Открытие двери через API Интерсвязи
- Поддержка авторизации через логин и пароль
- Автоматическое получение ID реле
- Реализовано в виде **кнопки** в Home Assistant

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
3. Введите ваш **логин** и **пароль** от личного кабинета Интерсвязь
4. После успешной авторизации появится **кнопка "Открыть домофон"**

## Примечания
- У каждого пользователя свой **RELAY_ID**, он получается автоматически
- Для работы необходим **доступ к интернету** и учетная запись Интерсвязи
- Интеграция использует API: `https://api.is74.ru/auth/mobile`

## Ошибки и предложения
Если у вас возникли проблемы или есть идеи для улучшения, создайте **issue** в [репозитории](https://github.com/USERNAME/intersvyaz_hass/issues).

---

© 2025, @hoolea

