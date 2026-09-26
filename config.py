from pathlib import Path

# Пути
BASE_OUTPUT_DIR = Path("./downloads")

# Настройки скачивания
MAX_PARALLEL_DOWNLOADS = 2  # Снижено для обхода DDoS-Guard
MAX_RETRIES_PER_IMAGE = 3
NETWORK_IDLE = 120000
IMAGE_DOWNLOAD_TIMEOUT = 60000  # 60 секунд на картинку

# Настройки "остывания" сервера
COOLDOWN_ON_FAILURE_SECONDS = 45  # Пауза, если глава скачалась не полностью
