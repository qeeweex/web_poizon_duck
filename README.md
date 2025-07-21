# Poizon Price Calculator

**Poizon Price Calculator** — это веб-приложение для расчета стоимости товаров с Poizon/Dewu с доставкой в Россию и отслеживания заказов.

---

## 🚀 Основные функции

### 1. Калькулятор цен
- Ввод ссылки на товар с Poizon/Dewu
- Автоматический расчет стоимости в рублях с учетом курса валют
- Добавление стоимости доставки
- Отображение полной итоговой цены

### 2. Отслеживание заказов
- Проверка статуса заказа по номеру
- Отображение текущего статуса
- История изменений

### 3. Админ-панель
- Управление курсом валют
- Установка стоимости доставки
- Добавление и редактирование заказов
- Изменение статусов заказов

---

## ⚡ Быстрый старт

1. Установите зависимости:
   ```bash
   pip install -r requirements.txt
   ```

2. Запустите приложение:
   ```bash
   python app.py
   ```

3. Откройте браузер и перейдите по адресу: [http://localhost:5000](http://localhost:5000)

---

## 🔑 Доступ к админ-панели

- **Логин:** `******`
- **Пароль:** `******`

#### Как изменить пароль администратора

1. Откройте файл `app.py`
2. Найдите строку с `generate_password_hash('admin123')`
3. Замените `'admin123'` на новый пароль
4. Удалите файл `database.db` (он будет создан заново при запуске)
5. Перезапустите приложение

---

## 📁 Структура проекта

```
web_poizon/
├── app.py                # Основное приложение Flask
├── database.db           # База данных SQLite (создается автоматически)
├── requirements.txt      # Зависимости Python
├── templates/            # HTML-шаблоны
│   ├── base.html
│   ├── index.html
│   ├── track.html
│   ├── admin_login.html
│   └── admin_panel.html
└── static/               # Статические файлы (CSS, JS, изображения)
```

---

## 🛒 Парсинг товаров с Poizon

В текущей версии функция парсинга реализована как заглушка: `parse_poizon_product` в `app.py`.

Для реальной интеграции потребуется:
- Получить доступ к API Poizon (если доступно)
- Реализовать веб-скрапинг для получения информации о товарах
- Использовать прокси для обхода гео-ограничений

**Пример веб-скрапинга:**

```python
import requests
from bs4 import BeautifulSoup

def parse_poizon_product(url):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    }
    try:
        response = requests.get(url, headers=headers)
        soup = BeautifulSoup(response.content, 'html.parser')
        product_name = soup.find('h1', class_='product-title').text.strip()
        price_cny = float(soup.find('span', class_='price').text.replace('¥', '').replace(',', ''))
        image_url = soup.find('img', class_='product-image')['src']
        return {
            'success': True,
            'product_name': product_name,
            'price_cny': price_cny,
            'image_url': image_url,
            'original_url': url
        }
    except Exception as e:
        return {
            'success': False,
            'error': str(e)
        }
```

---

## 🏗️ Развертывание

### Локальный запуск
```bash
python app.py
```

### На сервере (production)
1. Установите nginx или Apache
2. Настройте reverse proxy на порт 5000
3. Запустите через Gunicorn:
   ```bash
   pip install gunicorn
   gunicorn -w 4 -b 0.0.0.0:5000 app:app
   ```

---

## 💡 Возможные улучшения

- Кэширование данных товаров (например, через Redis)
- Асинхронная обработка запросов (Celery)
- Мониторинг и логирование
- Реализация REST API для мобильных приложений
- Email/SMS уведомления о статусе заказа
- Статистика и аналитика заказов

---

## 📄 Лицензия

Проект распространяется под лицензией MIT.

---

**Связь с автором:**  
Telegram: [@opiuumm6](https://t.me/opiuumm6)
