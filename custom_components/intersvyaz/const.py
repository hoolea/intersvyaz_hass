"""Constants for the Intersvyaz Domofon integration."""

DOMAIN = "intersvyaz"
BASE_URL = "https://api.is74.ru"
BASE_URL_CAM = "https://cams.is74.ru"

CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_PHONE = "phone"
CONF_AUTH_METHOD = "auth_method"
CONF_SMS_CODE = "sms_code"
CONF_ADDRESS = "address"
CONF_ADDRESSES = "addresses"
CONF_PRIMARY_USER_ID = "primary_user_id"
CONF_DEVICE_ID = "device_id"
CONF_AUTH_ID = "auth_id"
CONF_USER_ID = "user_id"
CONF_UUID = "uuid"
CONF_TOKEN = "token"
CONF_SCAN_INTERVAL = "scan_interval"

# Методы авторизации
AUTH_METHOD_LOGIN = "login"
AUTH_METHOD_PHONE = "phone"

# Шаги настройки для телефона
STEP_PHONE_NUMBER = "phone_number"
STEP_SMS_CODE = "sms_code"
STEP_ADDRESS_SELECT = "address_select"

# Домен CDN, отдающий снимки и потоки камер
BASE_URL_CDN = "https://cdn.cams.is74.ru"

# Ссылки на потоки (MEDIA.HLS.LIVE.*), приходящие от API, подписаны и живут
# ограниченное время, поэтому их нужно периодически перезапрашивать, а не
# кэшировать один раз при запуске Home Assistant.
DEFAULT_SCAN_INTERVAL = 120  # секунд
MIN_SCAN_INTERVAL = 30
MAX_SCAN_INTERVAL = 3600
