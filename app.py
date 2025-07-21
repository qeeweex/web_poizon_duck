from email.utils import format_datetime
from flask import Flask, render_template, request, jsonify, session, redirect, url_for
import requests
import re
import json
import sqlite3
import os
import hashlib
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from bs4 import BeautifulSoup
import time
import random
from urllib.parse import urljoin, urlparse
import urllib3
from requests_html import HTMLSession
import os
import time
import requests
import urllib3

# Отключаем предупреждения SSL
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = Flask(__name__)
app.secret_key = 'your-secret-key-here'  # Измените на случайный ключ

# Настройки Telegram-бота
TELEGRAM_BOT_TOKEN = '8110929140:AAHeoFeumGgYyfAizkSwPurfdCIiozqZwp0'
ADMIN_CHAT_ID = '6827811945'

# Функция для форматирования даты и времени
@app.template_filter('format_datetime')
def format_datetime(value, format='%d.%m.%Y'):
    """
    Форматирует datetime в удобный вид: ДД.ММ.ГГГГ ЧЧ:ММ
    """
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            # Пытаемся преобразовать строку в datetime
            value = datetime.strptime(value, '%Y-%m-%d %H:%M:%S')
        except ValueError:
            # Если не удалось, возвращаем как есть
            return value
    
    # Форматируем только дату
    return value.strftime(format)

# Инициализация базы данных
def init_db():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    # Проверяем и добавляем столбец telegram, если его нет
    try:
        cursor.execute("PRAGMA table_info(orders)")
        existing = [row[1] for row in cursor.fetchall()]
        if 'telegram' not in existing:
            cursor.execute("ALTER TABLE orders ADD COLUMN telegram TEXT")
    except sqlite3.OperationalError:
        pass  # таблица orders может не существовать на первом запуске
    
    # Таблица настроек (курс валют, доставка)
    # Таблица настроек (курс валют, доставка, процент администратора)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS settings (
            id INTEGER PRIMARY KEY,
            cny_rate REAL DEFAULT 13.5,
            delivery_cost REAL DEFAULT 1500,
            admin_percent REAL DEFAULT 5,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    # Добавляем колонку admin_percent, если она отсутствует в старых версиях
    cursor.execute("PRAGMA table_info(settings)")
    cols = [row[1] for row in cursor.fetchall()]
    if 'admin_percent' not in cols:
        cursor.execute("ALTER TABLE settings ADD COLUMN admin_percent REAL DEFAULT 5")

    # Таблица категорий доставки
    cursor.execute("DROP TABLE IF EXISTS delivery_categories")
    cursor.execute('''

        CREATE TABLE delivery_categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT UNIQUE,
            delivery_cost REAL
        )
    ''')
    
    # Добавляем начальные категории доставки
    initial_categories = [
        ('Кроссовки', 1500),
        ('Одежда', 1000),
        ('Аксессуары', 800)
    ]
    cursor.executemany('INSERT INTO delivery_categories (category_name, delivery_cost) VALUES (?, ?)', initial_categories)
    
    # Удаляем старую таблицу заказов, если она существует, чтобы создать со всеми нужными колонками
    cursor.execute('DROP TABLE IF EXISTS orders')
    
    # Таблица админов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS admins (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE,
            password_hash TEXT
        )
    ''')
    
    # Таблица заказа
    cursor.execute('''
    CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE,
        customer_name TEXT,
        product_name TEXT,
        telegram TEXT,
        url TEXT,
        price_cny REAL,
        price_rub REAL,
        delivery_cost REAL,
        admin_percent REAL,
        total_price REAL,
        image_url TEXT,
        status TEXT DEFAULT 'Создан',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
''')
    
    # Добавляем начальные настройки если их нет
    cursor.execute('SELECT COUNT(*) FROM settings')
    if cursor.fetchone()[0] == 0:
        cursor.execute('INSERT INTO settings (cny_rate, delivery_cost, admin_percent) VALUES (13.5, 1500, 5)')
    
    # Добавляем админа по умолчанию (admin/admin123)
    cursor.execute('SELECT COUNT(*) FROM admins')
    if cursor.fetchone()[0] == 0:
        password_hash = generate_password_hash('admin123')
        cursor.execute('INSERT INTO admins (username, password_hash) VALUES (?, ?)', 
                      ('admin', password_hash))
    
    conn.commit()
    conn.close()

def get_moscow_time():
    """Возвращает текущее время в московском часовом поясе (UTC+3)."""
    return (datetime.utcnow() + timedelta(hours=3)).strftime('%Y-%m-%d %H:%M:%S')

@app.route('/admin/delete_order', methods=['POST'])
def delete_order():
    """
    Удаляет заказ по его ID (только для админа)
    """
    if 'admin_logged_in' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})

    order_id = request.form.get('order_id')
    if not order_id:
        return redirect(url_for('admin_panel') + '?error=Идентификатор заказа не указан')

    try:
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        # Проверяем, существует ли заказ
        cursor.execute('SELECT order_number FROM orders WHERE id = ?', (order_id,))
        order = cursor.fetchone()
        if not order:
            conn.close()
            return redirect(url_for('admin_panel') + '?error=Заказ не найден')
        # Удаляем заказ
        cursor.execute('DELETE FROM orders WHERE id = ?', (order_id,))
        conn.commit()
        conn.close()
        return redirect(url_for('admin_panel') + '?message=Заказ успешно удалён')
    except Exception as e:
        print(f"Ошибка при удалении заказа: {e}")
        return redirect(url_for('admin_panel') + '?error=Ошибка при удалении заказа')

@app.route('/create_order', methods=['POST'])
def create_order():
    data = request.json or {}
    # Получаем цену из JSON: поддерживаем ключи price_cny или price
    # Рассчитываем цены: приводим к float и используем 0 по умолчанию
    settings = get_settings()
    price_cny = float(data.get('price_cny') or 0)
    price_rub = float(data.get('price_rub') or round(price_cny * settings['cny_rate'], 2))
    delivery_cost = float(data.get('delivery_cost') or settings['delivery_cost'])
    total_price = float(data.get('total_price') or round(price_rub + delivery_cost, 2))
    # Применяем процент администратора и сохраняем заказ
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    order_number = str(int(time.time()))
    customer = session.get('user_name', 'Гость')
    # Минимальный процент 5%
    admin_percent = max(settings.get('admin_percent', 10), 5)
    # Считаем базовую сумму без процента и итог с % администратора
    base_total = round(price_rub + delivery_cost, 2)
    # Админский процент от суммы (цена товара + доставка)
    admin_value = round((price_rub + delivery_cost) * admin_percent / 100, 2)
    final_price = round(base_total + admin_value, 2)
    # Debug: вывод деталей расчёта цены
    print(f"DEBUG PRICE CALC: base_total={base_total}, admin_percent={admin_percent}, admin_value={admin_value}, final_price={final_price}")
    cursor.execute(
        '''
        INSERT INTO orders (order_number, customer_name, product_name, telegram, url, price_cny, price_rub, delivery_cost, admin_percent, total_price, image_url, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            order_number,
            customer,
            data.get('product_name'),
            data.get('telegram'),
            data.get('url'),
            price_cny,
            price_rub,
            delivery_cost,
            admin_percent,
            final_price,
            data.get('image_url'),
            'Создан'
        )
    )
    conn.commit()
    # Отправляем уведомление администратору в Telegram
    try:
        client_telegram = data.get('telegram', 'не указан')
        # Отправляем Telegram с итоговой ценой, включая процент администратора
        message = (
            f"Новый заказ оформлен:\n"
            f"Ссылка: {data.get('url')}\n"
            f"Итоговая цена: {final_price} ₽\n"
            f"Telegram клиента: {client_telegram}"
        )
        # Отладочная информация для Telegram
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        params = {'chat_id': ADMIN_CHAT_ID, 'text': message}
        print(f"DEBUG: Sending Telegram message. URL: {url}, params: {params}")
        resp = requests.get(url, params=params)
        print(f"DEBUG: Telegram response status: {resp.status_code}, body: {resp.text}")
    except Exception as e:
        print(f"Ошибка при отправке уведомления в Telegram: {e}")
    conn.close()
    return jsonify({'success': True, 'order_number': order_number})

# Функция для получения настроек
def get_settings():
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT cny_rate, delivery_cost, admin_percent FROM settings WHERE id = 1')
    result = cursor.fetchone()
    conn.close()
    if result:
        return {'cny_rate': result[0], 'delivery_cost': result[1], 'admin_percent': result[2]}
    return {'cny_rate': 13.5, 'delivery_cost': 1500, 'admin_percent': 10}

# Фильтр для получения класса badge по статусу
def get_status_badge_class(status):
    status_classes = {
        'Создан': 'bg-secondary',  # Серый цвет
        'Оплачен': 'bg-info',  # Голубой цвет
        'В пути на склад в Китае': 'bg-warning',  # Желтый цвет
        'Прибыл на склад в Китае': 'bg-warning',  # Желтый цвет
        'Отправлен в РФ': 'bg-primary',  # Синий цвет
        'Прибыл в РФ': 'bg-primary',  # Синий цвет
        'Отправлен в ПВЗ': 'bg-success',  # Зеленый цвет
        'Готов к получению': 'bg-success'  # Зеленый цвет
    }
    return status_classes.get(status, 'bg-secondary')

# Регистрируем фильтр в Jinja2
app.jinja_env.filters['get_status_badge_class'] = get_status_badge_class

# Функция для валидации Poizon/Dewu URL
def is_valid_poizon_url(url):
    """
    Проверяет, является ли URL ссылкой на товар Poizon/Dewu
    Поддерживает различные домены и сокращенные ссылки
    """
    valid_domains = [
        'poizon.com', 'dewu.com', 'du.com',  # Основные домены
        'dw4.co', 'get.app',  # Сокращенные ссылки
        'm.poizon.com', 'm.dewu.com',  # Мобильные версии
        'app.poizon.com', 'app.dewu.com'  # Приложения
    ]
    
    url_lower = url.lower()
    return any(domain in url_lower for domain in valid_domains)

# Функция для получения реального URL из сокращенной ссылки
def resolve_shortened_url(url):
    """
    Разворачивает сокращенные ссылки для получения финального URL
    Пробует несколько методов
    """
    try:
        # Сначала простая попытка
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        response = requests.head(url, allow_redirects=True, timeout=15, headers=headers, verify=False)
        if response.url != url:
            print(f"🔗 Ссылка развернута: {url} -> {response.url}")
            return response.url
        
        # Если HEAD не сработал, пробуем GET с минимальной загрузкой
        response = requests.get(url, allow_redirects=True, timeout=15, headers=headers, verify=False, stream=True)
        final_url = response.url
        response.close()
        
        if final_url != url:
            print(f"🔗 Ссылка развернута через GET: {url} -> {final_url}")
            return final_url
            
    except Exception as e:
        print(f"⚠️ Не удалось развернуть ссылку: {e}")
    
    return url

def parse_poizon_product(url):
    print(f"🔍 Обрабатываем URL: {url}")

    # 1. Сначала пробуем через requests-html и API (самый точный способ)
    try:
        product_data = parse_poizon_with_playwright(url)
        if product_data and product_data.get('success') and product_data.get('price_cny'):
            print(f"[requests-html/API] ✅ Успешно: {product_data['product_name']} ¥{product_data['price_cny']}")
            return product_data
    except Exception as e:
        print(f"[requests-html/API] Ошибка: {e}")

    # 2. Fallback: обычный requests + BeautifulSoup
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }
        resolved_url = resolve_shortened_url(url)
        resp = requests.get(resolved_url, headers=headers, timeout=15, verify=False)
        if resp.status_code == 200 and len(resp.content) > 1000:
            html = resp.text
            soup = BeautifulSoup(html, 'html.parser')
            product_data = extract_product_data_comprehensive(soup, html, resolved_url)
            if product_data and product_data.get('name') and product_data.get('price'):
                print(f"[requests+BS4] ✅ Успешно: {product_data['name']} ¥{product_data['price']}")
                return {
                    'success': True,
                    'product_name': product_data['name'],
                    'price_cny': float(product_data['price']),
                    'image_url': product_data.get('image') or 'https://via.placeholder.com/300x300?text=Нет+изображения',
                    'resolved_url': resolved_url
                }
            else:
                print('[requests+BS4] ⚠️ Недостаточно данных.')
    except Exception as e:
        print(f"[requests+BS4] Ошибка: {e}")

    print("❌ Не удалось получить данные через requests+BeautifulSoup.")
    return {
        'success': False,
        'error': 'Не удалось получить данные о товаре (fallback через requests+BeautifulSoup).'
    }

def debug_print_prices(soup):
    print("=== DEBUG: Поиск всех цен на странице ===")
    for tag in soup.find_all(['span', 'div']):
        text = tag.get_text(strip=True)
        if '¥' in text or '￥' in text:
            print(text)
    print("=== END DEBUG ===")

def extract_product_data_comprehensive(soup, html, url):
    """
    Универсальный сбор данных о товаре из HTML и JSON.
    """
    result = {'name': None, 'price': None, 'image': None}

    # --- Название товара ---
    name = None
    title_div = soup.find('div', class_='title  ')
    if title_div and title_div.get_text(strip=True):
        name = title_div.get_text(strip=True)
    if not name:
        h1 = soup.find('h1')
        if h1 and h1.get_text(strip=True):
            name = h1.get_text(strip=True)
    if not name:
        alt_title = soup.select_one('div[class*=title], .product-title, .detail-title')
        if alt_title and alt_title.get_text(strip=True):
            name = alt_title.get_text(strip=True)
    if not name:
        meta_title = soup.find('meta', {'property': 'og:title'})
        if meta_title and meta_title.get('content'):
            name = meta_title.get('content').strip()
    if name:
        result['name'] = name

    # --- Цена товара ---
    price = None
    # Специальный поиск цены из текста '发售价格' в raw HTML
    m_html = re.search(r'发售价格[¥￥]?\s*([\d,]+(?:\.\d+)?)', html)
    if m_html:
        try:
            price = float(m_html.group(1).replace(',', ''))
            print(f"🔍 Найдена цена '发售价格' в raw HTML: {price}")
        except:
            price = None
    # Если нашли через '发售价格', сразу возвращаем
    if price:
        result['price'] = round(price, 2)
        return result

    # 1. Ищем цену в приоритетных блоках с классами, связанными с ценой
    price_block = soup.select_one('.price, .amount, [class*=price], [class*=amount]')
    if price_block:
        print(f"🔍 Найден блок цены: {price_block.get_text(strip=True)}")
        price_val = re.search(r'[¥￥]?\s?(\d{2,6})', price_block.get_text())
        if price_val:
            price = float(price_val.group(1))

    # 2. Ищем цену рядом с кнопкой "Купить" (например, "立即购买")
    if not price:
        buy_btn = soup.find(string=re.compile(r'立即购买|Купить|Buy Now', re.I))
        if buy_btn:
            parent = buy_btn.find_parent()
            if parent:
                print(f"🔍 Найден блок рядом с кнопкой 'Купить': {parent.get_text(strip=True)}")
                price_texts = parent.find_all(string=re.compile(r'[¥￥]?\s?\d{2,6}'))
                for t in price_texts:
                    price_val = re.search(r'[¥￥]?\s?(\d{2,6})', t)
                    if price_val:
                        price = float(price_val.group(1))
                        break

    # 3. Ищем цену в JavaScript-данных
    if not price:
        script_tags = soup.find_all('script')
        for script in script_tags:
            if script.string and 'price' in script.string.lower():
                print(f"🔍 Найден скрипт с данными цены: {script.string[:100]}...")
                price_match = re.search(r'"price"\s*:\s*"?(?P<price>\d{2,6})', script.string)
                if price_match:
                    price = float(price_match.group('price'))
                    break

    # 4. Meta-теги с ценой
    if not price:
        price_meta = soup.find('meta', {'property': 'product:price:amount'}) or soup.find('meta', {'name': 'price'})
        if price_meta and price_meta.get('content'):
            print(f"🔍 Найден meta-тег с ценой: {price_meta.get('content')}")
            try:
                price = float(re.sub(r'[^\d.]', '', price_meta.get('content')))
            except:
                pass

    # 5. НОВЫЙ ФАЛЬБЭК: берем первую цену, встреченную на странице
    texts = []
    for tag in soup.find_all(['span', 'div']):
        text = tag.get_text(strip=True)
        if '¥' in text or '￥' in text:
            texts.append(text)
    if texts:
        first_text = texts[0]
        price_match = re.search(r'[¥￥]\s*([\d,]+(?:\.\d+)?)', first_text)
        if price_match:
            try:
                tmp = float(price_match.group(1).replace(',', ''))
                price = tmp
                print(f"🔍 Берём первую цену на странице: {price}")
            except:
                pass
    if price:
        result['price'] = round(price, 2)

    # --- Проверка диапазона цены ---
    if price and not (500 <= price <= 10000):  # Уточняем диапазон для корректных цен
        print(f"⚠️ Найдена некорректная цена: {price}")
        price = None

    if price:
        result['price'] = round(price, 2)

    # --- Главное фото товара ---
    image = None
    # 1. Поиск по dewucdn.com/pro-img или dewucdn.com/detail-img
    imgs = soup.find_all('img')
    for img in imgs:
        src = img.get('src')
        if src and src.startswith('http') and (
            'dewucdn.com/pro-img' in src or 'dewucdn.com/detail-img' in src
        ):
            image = src
            break
    # 2. Если не нашли — старый способ (оставьте как резерв)
    if not image:
        image_big_div = soup.find('div', class_='image-big')
        if image_big_div:
            img = image_big_div.find('img')
            if img and img.get('src') and img.get('src').startswith('http'):
                image = img.get('src')
    if not image:
        meta_img = soup.find('meta', {'property': 'og:image'})
        if meta_img and meta_img.get('content'):
            image = meta_img.get('content')
    if image:
        result['image'] = image

    return result

def extract_from_html_elements(soup, url):
    """
    Извлекает данные товара из HTML элементов (пытается взять цену из главного блока, как на сайте)
    """
    result = {'name': None, 'price': None, 'image': None}

    # --- Название товара ---
    name = None
    title_div = soup.find('div', class_='title  ')
    if title_div and title_div.get_text(strip=True):
        name = title_div.get_text(strip=True)
    if not name:
        h1 = soup.find('h1')
        if h1 and h1.get_text(strip=True):
            name = h1.get_text(strip=True)
    if not name:
        alt_title = soup.select_one('div[class*=title], .product-title, .detail-title')
        if alt_title and alt_title.get_text(strip=True):
            name = alt_title.get_text(strip=True)
    if not name:
        meta_title = soup.find('meta', {'property': 'og:title'})
        if meta_title and meta_title.get('content'):
            name = meta_title.get('content').strip()
    if name:
        result['name'] = name

    # --- Цена товара: ищем в приоритетных блоках ---
    price = None

    # 1. Ищем цену в блоках с классами, связанными с ценой
    price_block = soup.select_one('.price, .amount, [class*=price], [class*=amount]')
    if price_block:
        price_val = re.search(r'[¥￥]?\s?(\d{2,5})', price_block.get_text())
        if price_val:
            price = float(price_val.group(1))

    # 2. Ищем цену рядом с кнопкой "Купить" (например, "立即购买")
    if not price:
        buy_btn = soup.find(string=re.compile(r'立即购买|Купить|Buy Now', re.I))
        if buy_btn:
            parent = buy_btn.find_parent()
            if parent:
                price_texts = parent.find_all(string=re.compile(r'[¥￥]?\s?\d{2,5}'))
                for t in price_texts:
                    price_val = re.search(r'[¥￥]?\s?(\d{2,5})', t)
                    if price_val:
                        price = float(price_val.group(1))
                        break

    # 3. Fallback: выбираем первую корректную цену из всех найденных блоков
    if not price:
        price_candidates = []
        for tag in soup.find_all(['span', 'div']):
            text = tag.get_text(strip=True)
            found = re.findall(r'[¥￥]\s?(\d{2,5})', text)
            for val in found:
                try:
                    p = float(val)
                    if 10 <= p <= 1000:  # Уточните диапазон, если нужно
                        price_candidates.append(p)
                except:
                    continue
        if price_candidates:
            price = price_candidates[0]  # Берём первую подходящую цену

    # --- Проверка диапазона цены ---
    if price and not (10 <= price <= 1000):  # Уточните диапазон, если нужно
        print(f"⚠️ Найдена некорректная цена: {price}")
        price = None

    if price:
        result['price'] = round(price, 2)

    # --- Главное фото товара ---
    image = None
    # 1. Поиск по dewucdn.com/pro-img или dewucdn.com/detail-img
    imgs = soup.find_all('img')
    for img in imgs:
        src = img.get('src')
        if src and src.startswith('http') and (
            'dewucdn.com/pro-img' in src or 'dewucdn.com/detail-img' in src
        ):
            image = src
            break
    # 2. Если не нашли — старый способ (оставьте как резерв)
    if not image:
        image_big_div = soup.find('div', class_='image-big')
        if image_big_div:
            img = image_big_div.find('img')
            if img and img.get('src') and img.get('src').startswith('http'):
                image = img.get('src')
    if not image:
        meta_img = soup.find('meta', {'property': 'og:image'})
        if meta_img and meta_img.get('content'):
            image = meta_img.get('content')
    if image:
        result['image'] = image

    return result

def extract_from_json_data(soup, html_text):
    """
    Извлекает данные из JSON объектов в HTML (window.__data__, window.g_config и т.д.)
    """
    import json
    import re
    
    # Паттерны для поиска JSON данных
    json_patterns = [
        r'window\.__INITIAL_STATE__\s*=\s*({.+?});',
        r'window\.__data__\s*=\s*({.+?});',
        r'window\.g_config\s*=\s*({.+?});',
        r'__NEXT_DATA__["\']:\s*({.+?})',
        r'application/json["\']>({.+?})</script>',
        r'type=["\']application/ld\+json["\'][^>]*>({.+?})</script>'
    ]
    
    for pattern in json_patterns:
        matches = re.findall(pattern, html_text, re.DOTALL)
        for match in matches:
            try:
                data = json.loads(match)
                result = extract_from_json_object(data)
                if result and result.get('name') and result.get('price'):
                    return result
            except json.JSONDecodeError:
                continue
    
    return None

def extract_from_json_object(data, path=""):
    """
    Рекурсивно ищет данные товара в JSON объекте
    """
    if isinstance(data, dict):
        result = {'name': None, 'price': None, 'image': None}
        
        # Ищем поля с названием товара
        for key, value in data.items():
            key_lower = str(key).lower()
            if any(name_key in key_lower for name_key in ['title', 'name', 'product_name', 'goods_name', 'item_name']):
                if isinstance(value, str) and len(value.strip()) > 0:
                    result['name'] = value.strip()
            
            # Ищем поля с ценой
            elif any(price_key in key_lower for price_key in ['price', 'cost', 'amount', 'yuan', 'cny']):
                if isinstance(value, (int, float)) and 10 < value < 100000:
                    result['price'] = float(value)
                elif isinstance(value, str):
                    price = extract_price_from_text(value)
                    if price:
                        result['price'] = price
            
            # Iщем поля с изображением
            elif any(img_key in key_lower for img_key in ['image', 'img', 'photo', 'picture', 'thumbnail']):
                if isinstance(value, str) and ('http' in value or '/' in value):
                    result['image'] = make_absolute_url(value, "")
        
        # Если не нашли в корне, ищем рекурсивно
        if not (result.get('name') and result.get('price')):
            for key, value in data.items():
                if isinstance(value, (dict, list)):
                    nested_result = extract_from_json_object(value, path + "." + str(key))
                    if nested_result and nested_result.get('name') and nested_result.get('price'):
                        return nested_result
        
        return result if (result.get('name') and result.get('price')) else None
    
    elif isinstance(data, list):
        for i, item in enumerate(data):
            result = extract_from_json_object(item, path + f"[{i}]")
            if result and result.get('name') and result.get('price'):
                return result
    
    return None

def extract_from_meta_tags(soup):
    """
    Извлекает данные из meta тегов Open Graph и других
    """
    result = {'name': None, 'price': None, 'image': None}
    
    # Meta теги для названия
    title_meta = soup.find('meta', {'property': 'og:title'}) or soup.find('meta', {'name': 'title'})
    if title_meta and title_meta.get('content'):
        result['name'] = title_meta.get('content').strip()
    
    # Meta теги для изображения
    image_meta = soup.find('meta', {'property': 'og:image'}) or soup.find('meta', {'name': 'image'})
    if image_meta and image_meta.get('content'):
        result['image'] = image_meta.get('content')
    
    # Meta теги для цены
    price_meta = soup.find('meta', {'property': 'product:price:amount'}) or soup.find('meta', {'name': 'price'})
    if price_meta and price_meta.get('content'):
        price = extract_price_from_text(price_meta.get('content'))
        if price:
            result['price'] = price
    
    return result

def extract_price_from_text(price_text):
    """Извлекает числовую цену из текста (корректно обрабатывает запятые и пробелы)"""
    if not price_text:
        return None

    # Удаляем всё кроме цифр и разделителей
    text = price_text.replace(' ', '').replace('￥', '').replace('¥', '').replace('元', '')
    text = text.replace('CNY', '').replace('价格：', '').replace('售价', '').strip()

    # Удаляем нечисловые символы, но сохраняем запятую и точку
    clean = re.sub(r'[^\d.,]', '', text)

    # Если число в виде 1,234.56 — оставим как есть
    if ',' in clean and '.' in clean:
        clean = clean.replace(',', '')  # Убираем запятую, как разделитель тысяч
    elif ',' in clean:
        clean = clean.replace(',', '.')  # Запятая как разделитель дробной части

    try:
        price = float(clean)
        # Исключаем заведомо мусорные значения (слишком длинные)
        if 10 < price < 100000:
            return price
        else:
            return None
    except:
        return None

def make_absolute_url(url, base_url):
    """Преобразует относительный URL в абсолютный"""
    if not url:
        return None
    
    # Если URL уже абсолютный
    if url.startswith('http'):
        return url
    
    # Если URL начинается с //, добавляем протокол
    if url.startswith('//'):
        return 'https:' + url
    
    # Если есть базовый URL, используем urljoin
    if base_url:
        return urljoin(base_url, url)
    
    return url

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/calculate_price', methods=['POST'])
def calculate_price():

    data = request.get_json()
    raw_text = data.get('url', '').strip()
    # Извлекаем ссылку из произвольного текста
    m = re.search(r'(https?://[^\s,，]+)', raw_text)
    poizon_url = m.group(1) if m else raw_text
    
    if not poizon_url:
        return jsonify({'success': False, 'error': 'Ссылка не может быть пустой'})
    
    # Проверяем, что это ссылка на Poizon/Dewu
    if not is_valid_poizon_url(poizon_url):
        return jsonify({'success': False, 'error': 'Неверная ссылка. Используйте ссылки на товары с Poizon/Dewu'})
    
    # Парсим товар (только имя и изображение)
    product_data = parse_poizon_product(poizon_url)
    if not product_data.get('success'):
        return jsonify({'success': False, 'error': product_data.get('error', 'Не удалось получить данные о товаре')})
    
    # Получаем настройки и категории доставки
    settings = get_settings()
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT category_name, delivery_cost FROM delivery_categories ORDER BY id')
    delivery_categories = cursor.fetchall()
    conn.close()
    if not delivery_categories:
        delivery_categories = [('Стандартная доставка', settings['delivery_cost'])]

    # Рассчитываем цену и возвращаем все данные
    price_cny = product_data.get('price_cny') or 0
    price_rub = round(price_cny * settings['cny_rate'], 2)
    default_delivery = delivery_categories[0][1] if delivery_categories else settings['delivery_cost']
    total_price = round(price_rub + default_delivery, 2)
    return jsonify({
        'success': True,
        'product_name': product_data['product_name'],
        'image_url': product_data['image_url'],
        'price_cny': price_cny,
        'price_rub': price_rub,
        'delivery_cost': default_delivery,
        'total_price': total_price,
        'delivery_categories': [{'name': cat[0], 'cost': cat[1]} for cat in delivery_categories],
        'cny_rate': settings['cny_rate'],
        'admin_percent': settings['admin_percent'],
        'resolved_url': product_data.get('resolved_url', poizon_url)
    })

@app.route('/track')
def track_order():
    return render_template('track.html')

@app.route('/check_order', methods=['POST'])
def check_order():
    data = request.get_json()
    order_number = data.get('order_number', '').strip()
    
    if not order_number:
        return jsonify({'success': False, 'error': 'Номер заказа не может быть пустым'})
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT order_number, customer_name, product_name, telegram, status, updated_at FROM orders WHERE order_number = ?', 
                   (order_number,))
    result = cursor.fetchone()
    conn.close()
    
    if result:
        return jsonify({
            'success': True,
            'order_number': result[0],
            'customer_name': result[1],
            'product_name': result[2],
            'telegram': result[3],
            'status': result[4],
            'updated_at': result[5]
        })
    else:
        return jsonify({'success': False, 'error': 'Заказ не найден'})

@app.route('/admin')
def admin_login():
    if 'admin_logged_in' in session:
        return redirect(url_for('admin_panel'))
    return render_template('admin_login.html')

@app.route('/admin/login', methods=['POST'])
def admin_login_post():
    username = request.form['username']
    password = request.form['password']
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('SELECT password_hash FROM admins WHERE username = ?', (username,))
    result = cursor.fetchone()
    conn.close()
    
    if result and check_password_hash(result[0], password):
        session['admin_logged_in'] = True
        return redirect(url_for('admin_panel'))
    else:
        return render_template('admin_login.html', error='Неверный логин или пароль')

@app.route('/admin/panel')
def admin_panel():
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_login'))
    
    settings = get_settings()
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # Получаем заказы (с ценой в CNY)
    # Получаем заказы с отображением процента администратора
    cursor.execute('SELECT order_number, customer_name, product_name, telegram, total_price, admin_percent, status, created_at, updated_at, id FROM orders ORDER BY created_at DESC')
    orders = cursor.fetchall()
    
    # Получаем категории доставки
    cursor.execute('SELECT id, category_name, delivery_cost FROM delivery_categories ORDER BY id')
    delivery_categories = cursor.fetchall()
    
    conn.close()
    
    return render_template('admin_panel.html', 
                           settings=settings, 
                           orders=orders, 
                           delivery_categories=delivery_categories)

@app.route('/admin/update_settings', methods=['POST'])
def update_settings():
    if 'admin_logged_in' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})

    # Используем get() метод, чтобы избежать KeyError при отсутствии поля
    cny_rate = float(request.form.get('cny_rate', 0))
    
    # Получаем текущие настройки для использования значений по умолчанию
    current_settings = get_settings()
    
    # Безопасно получаем delivery_cost и admin_percent из формы, используя текущее значение как запасной вариант
    try:
        delivery_cost = float(request.form.get('delivery_cost', current_settings['delivery_cost']))
    except (ValueError, TypeError):
        delivery_cost = current_settings['delivery_cost']
    try:
        admin_percent = float(request.form.get('admin_percent', current_settings['admin_percent']))
    except (ValueError, TypeError):
        admin_percent = current_settings['admin_percent']
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE settings SET cny_rate = ?, delivery_cost = ?, admin_percent = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1',
                   (cny_rate, delivery_cost, admin_percent))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_panel'))

@app.route('/admin/add_order', methods=['POST'])
def add_order():
    if 'admin_logged_in' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    order_number = request.form['order_number']
    product_name = request.form['product_name']
    telegram = request.form['telegram']
    status = request.form['status']
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO orders (order_number, product_name, telegram, status) VALUES (?, ?, ?, ?)',
            (order_number, product_name, telegram, status)
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.close()
        return redirect(url_for('admin_panel') + '?error=Заказ с таким номером уже существует')
    conn.close()
    
    return redirect(url_for('admin_panel'))

@app.route('/admin/update_order', methods=['POST'])
def update_order():
    if 'admin_logged_in' not in session:
        return jsonify({'success': False, 'error': 'Не авторизован'})
    
    order_id = request.form['order_id']
    status = request.form['status']
    
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    cursor.execute('UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?',
                   (status, order_id))
    conn.commit()
    conn.close()
    
    return redirect(url_for('admin_panel'))

@app.route('/admin/logout')
def admin_logout():
    session.pop('admin_logged_in', None)
    return redirect(url_for('index'))

@app.route('/admin/update_delivery_categories', methods=['POST'])
def update_delivery_categories():
    """
    Обновляет категории доставки и их стоимость
    """
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_login'))
    
    try:
        # Получаем данные из формы
        categories = {}
        for key, value in request.form.items():
            if key.startswith('category_name_'):
                category_id = key.replace('category_name_', '')
                cost_key = f'delivery_cost_{category_id}'
                
                name = request.form.get(key, '').strip()
                
                # Безопасное преобразование стоимости доставки с проверкой на пустые значения
                cost_str = request.form.get(cost_key, '').strip()
                try:
                    cost = float(cost_str) if cost_str else 0
                except ValueError:
                    cost = 0
                
                # Добавляем категорию, только если указано имя и стоимость > 0
                if name and cost > 0:
                    categories[category_id] = {
                        'name': name,
                        'cost': cost
                    }
        
        # Обновляем БД
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        
        # Очищаем таблицу категорий
        cursor.execute('DELETE FROM delivery_categories')
        
        # Вставляем новые данные
        for category_id, data in categories.items():
            cursor.execute(
                'INSERT INTO delivery_categories (category_name, delivery_cost) VALUES (?, ?)',
                (data['name'], data['cost'])
            )
        
        conn.commit()
        conn.close()
        
        return redirect(url_for('admin_panel') + '?message=Категории доставки успешно обновлены')
    except Exception as e:
        print(f"Ошибка при обновлении категорий доставки: {e}")
        return redirect(url_for('admin_panel') + '?error=Ошибка при обновлении категорий доставки: ' + str(e))

@app.route('/admin/delete_delivery_category', methods=['POST'])
def delete_delivery_category():
    """
    Удаляет категорию доставки по её ID
    """
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_login'))
    
    try:
        category_id = request.form.get('category_id')
        
        if not category_id:
            return redirect(url_for('admin_panel') + '?error=Идентификатор категории не указан')
        
        conn = sqlite3.connect('database.db')
        cursor = conn.cursor()
        
        # Сначала проверяем, существует ли категория
        cursor.execute('SELECT category_name FROM delivery_categories WHERE id = ?', (category_id,))
        category = cursor.fetchone()
        
        if not category:
            conn.close()
            return redirect(url_for('admin_panel') + '?error=Категория не найдена')
        
        # Удаляем категорию
        cursor.execute('DELETE FROM delivery_categories WHERE id = ?', (category_id,))
        conn.commit()
        conn.close()
        
        return redirect(url_for('admin_panel') + '?message=Категория доставки успешно удалена')
    except Exception as e:
        print(f"Ошибка при удалении категории доставки: {e}")
        return redirect(url_for('admin_panel') + '?error=Ошибка при удалении категории доставки')

def try_alternative_parsing_methods(url):
    """
    Пробует альтернативные методы парсинга когда основные не работают
    """
    try:
        # Метод 1: Попытка через мобильную версию
        mobile_urls = []
        parsed = urlparse(url)
        base_domain = parsed.netloc
        
        if 'dewu.com' in base_domain or 'poizon.com' in base_domain:
            mobile_urls = [
                url.replace('www.', 'm.'),
                url.replace('dewu.com', 'm.dewu.com'),
                url.replace('poizon.com', 'm.poizon.com'),
                url.replace('https://', 'https://m.')
            ]
        
        for mobile_url in mobile_urls:
            if mobile_url != url:
                print(f"📱 Пробуем мобильную версию: {mobile_url}")
                result = simple_parse_attempt(mobile_url)
                if result:
                    return result
        
        # Метод 2: Попытка через различные поддомены
        subdomains = ['app', 'api', 'mobile', 'wap']
        for subdomain in subdomains:
            test_url = f"https://{subdomain}.{parsed.netloc.replace('www.', '')}{parsed.path}"
            print(f"🔗 Пробуем поддомен: {test_url}")
            result = simple_parse_attempt(test_url)
            if result:
                return result
                
    except Exception as e:
        print(f"⚠️ Ошибка в альтернативных методах: {e}")
    
    return None

def try_api_approach(url):
    """
    Пытается найти API endpoints для получения данных о товаре
    """
    try:
        # Извлекаем ID товара из URL
        product_id = extract_product_id_from_url(url)
        if not product_id:
            return None
        
        print(f"🆔 Найден ID товара: {product_id}")
        
        # Пробуем различные API endpoints
        parsed = urlparse(url)
        base_domain = parsed.netloc.replace('www.', '').replace('m.', '')
        
        api_endpoints = [
            f"https://api.{base_domain}/product/{product_id}",
            f"https://app.{base_domain}/api/product/{product_id}",
            f"https://{base_domain}/api/v1/product/{product_id}",
            f"https://{base_domain}/api/product/detail/{product_id}"
        ]
        
        for endpoint in api_endpoints:
            print(f"🔌 Пробуем API: {endpoint}")
            result = try_api_endpoint(endpoint, product_id)
            if result:
                return result
                
    except Exception as e:
        print(f"⚠️ Ошибка в API подходе: {e}")
    
    return None

def try_api_endpoint(endpoint, product_id):
    """
    Пробует получить данные о товаре через API endpoint
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1',
            'Accept': 'application/json, text/plain, */*',
            'Referer': endpoint,
            'Connection': 'keep-alive',
        }
        response = requests.get(endpoint, headers=headers, timeout=15, verify=False)
        if response.status_code == 200 and response.headers.get('Content-Type', '').startswith('application/json'):
            data = response.json()
            # Попробуем найти название, цену и картинку в json
            result = extract_from_json_object(data)
            if result and result.get('name') and result.get('price'):
                return result
    except Exception as e:
        print(f"⚠️ Ошибка запроса к API endpoint: {e}")
    return None

def simple_parse_attempt(url):
    """
    Простая попытка парсинга с минимальными заголовками
    """
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 11; SM-A515F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'zh-CN,zh;q=0.5',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1'
        }
        
        response = requests.get(url, headers=headers, timeout=20, verify=False)
        if response.status_code == 200 and len(response.content) > 1000:
            soup = BeautifulSoup(response.content, 'html.parser')
            return extract_product_data_comprehensive(soup, response.text, url)
    except:
        pass
    return None

def extract_product_id_from_url(url):
    
    """
    Извлекает ID товара из URL
    """
    # Паттерны для разных форматов URL
    patterns = [
        r'/product/(\d+)',
        r'spuId=(\d+)',
        r'id=(\d+)',
        r'/detail/(\d+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    
    return None

# --- Selenium headless Chrome для обхода защиты ---
# def selenium_get_html(url, proxy=None):
#     """
#     Получение HTML через Selenium с эмуляцией мобильного устройства.
#     """
#     from selenium import webdriver
#     from selenium.webdriver.chrome.options import Options

#     print(f"🌐 Получаем HTML через Selenium для URL: {url}")

#     chrome_options = Options()
#     chrome_options.add_argument("--headless")
#     chrome_options.add_argument("--disable-gpu")
#     chrome_options.add_argument("--no-sandbox")

#     # Эмуляция мобильного устройства
#     mobile_emulation = {"deviceName": "iPhone X"}
#     chrome_options.add_experimental_option("mobileEmulation", mobile_emulation)

#     if proxy:
#         chrome_options.add_argument(f"--proxy-server={proxy}")

#     driver = webdriver.Chrome(options=chrome_options)

#     try:
#         driver.get(url)
#         time.sleep(5)  # Ожидание загрузки страницы
#         html = driver.page_source
#         print("✅ HTML успешно получен через эмуляцию мобильного устройства.")
#         return html
#     except Exception as e:
#         print(f"⚠️ Ошибка при получении HTML через Selenium: {e}")
#         return None
#     finally:
#         driver.quit()

# --- ВСТАВИТЬ В КОНЕЦ parse_poizon_product ---
# После всех попыток через requests:
# print('Пробуем получить страницу через Selenium...')
# html = selenium_get_html(resolved_url)
# if html:
#     soup = BeautifulSoup(html, 'html.parser')
#     product_data = extract_product_data_comprehensive(soup, html, resolved_url)
#     if product_data and product_data.get('name') and product_data.get('price'):
#         print(f"[Selenium] Успешно: {product_data['name']} ¥{product_data['price']}")
#         return {
#             'success': True,
#             'product_name': product_data['name'],
#             'price_cny': float(product_data['price']),
#             'image_url': product_data.get('image') or 'https://via.placeholder.com/300x300?text=Нет+изображения',
#             'resolved_url': resolved_url
#         }
# print('[Selenium] Не удалось получить данные через браузер.')
# return {'success': False, 'error': 'Не удалось получить данные даже через браузер (Selenium).'}

def get_valid_price(val):
    try:
        price = float(str(val).replace(',', '').replace('￥', '').replace('¥', '').strip())
        if 10 < price < 100000:
            return price
    except:
        pass
    return None


def get_html_playwright(url):
    from playwright.sync_api import sync_playwright
    html = None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(user_agent="Mozilla/5.0 (iPhone; CPU iPhone OS 16_6 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.6 Mobile/15E148 Safari/604.1")
        page.goto(url, timeout=60000)
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()
    return html

def parse_poizon_with_playwright(url):
    import re, requests, json
    from bs4 import BeautifulSoup

    def get_valid_price(v):
        try:
            val = float(str(v).replace(',', '').strip())
            return val if 50 < val < 100000 else None
        except:
            return None

    def extract_price_from_text(text):
        if not text:
            return None
        match = re.search(r'¥?\s?(\d{3,6})', text.replace(',', '').replace('￥', '').replace('¥', ''))
        if match:
            return get_valid_price(match.group(1))
        return None

    # --- 1. Разворачиваем ссылку и получаем HTML через Playwright ---
    resolved_url = resolve_shortened_url(url)
    html = get_html_playwright(resolved_url)
    soup = BeautifulSoup(html, 'html.parser')

    # --- 2. Название товара ---
    name = None
    h1 = soup.find('h1')
    if h1:
        name = h1.get_text(strip=True)
    else:
        title = soup.select_one('.product-title')
        if title:
            name = title.get_text(strip=True)

    # --- 3. Поиск itemId/spuId ---
    item_id = None
    for script in soup.find_all("script"):
        text = script.string or ""
        m = re.search(r'"(?:spuId|itemId)"\s*:\s*"?(?P<id>\d{6,})"', text)
        if m:
            item_id = m.group("id")
            break
    if not item_id:
        m = re.search(r'(?:spuId|itemId)[=|/](\d+)', resolved_url)
        if m:
            item_id = m.group(1)

    # --- 4. Цена: сначала пробуем API ---
    price = None
    if item_id:
        try:
            api_url = f"https://app.dewu.com/api/v1/product/detail?spuId={item_id}"
            headers = {"User-Agent": "Mozilla/5.0"}
            response = requests.get(api_url, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                sku_list = data.get('data', {}).get('skuList')
                prices = []

                if sku_list:
                    for sku in sku_list:
                        stock = sku.get('stockNum', 0)
                        if int(stock) > 0:
                            sku_price = sku.get('skuPrice') or sku_price.get('salePrice')
                            val = sku_price.get('actualSalePrice') or sku_price.get('salePrice')
                            p = get_valid_price(val)
                            if p:
                                prices.append(p / 100)  # делим фэни → юани

                if prices:
                    price = min(prices)
                    print(f"✅ Цена из API: ¥{price}")

            else:
                print(f"⚠️ API недоступен: {response.status_code}")

        except Exception as e:
            print(f"❌ Ошибка API-запроса: {e}")

    # --- 5. Fallback: парсим цену из DOM, если API не сработал ---
    if not price:
        print("🔁 Парсим цену из DOM")
        possible_prices = []
        for tag in soup.find_all(['span', 'div']):
            text = tag.get_text(strip=True)
            val = extract_price_from_text(text)
            if val:
                possible_prices.append(val)
        if possible_prices:
            price = max(possible_prices)
            print(f"✅ Цена из DOM: ¥{price}")

    # --- 5.1 Пробуем найти цену около кнопки "购买" ---
    if not price:
        buy_button = soup.find(string=re.compile(r'立即购买|购买'))
        if buy_button:
            container = buy_button.find_parent()
            if container:
                for span in container.find_parents()[0].select('span'):
                    text = span.get_text(strip=True)
                    p = extract_price_from_text(text)
                    if p:
                        price = p
                        break

    # --- 6. Картинка: только из detailImg или spuImgList в API ---
    image = None
    if item_id:
        try:
            api_url = f"https://app.dewu.com/api/v1/product/detail?spuId={item_id}"
            response = requests.get(api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
            if response.status_code == 200:
                data = response.json()
                detail_imgs = data.get('data', {}).get('detailImg')
                spu_imgs = data.get('data', {}).get('spuImgList')
                print('detailImg:', detail_imgs)
                print('spuImgList:', spu_imgs)
                # 1. detailImg (массив с фото товара)
                if isinstance(detail_imgs, list) and detail_imgs:
                    image = detail_imgs[0]
                # 2. spuImgList (ещё один массив с фото)
                if not image and isinstance(spu_imgs, list) and spu_imgs:
                    image = spu_imgs[0]
                # 3. imgUrl (старый способ)
                if not image:
                    image = data.get('data', {}).get('imgUrl')
                    if isinstance(image, list):
                        image = image[0] if image else None
                # --- Делаем ссылку абсолютной ---
                if image and image.startswith('//'):
                    image = 'https:' + image
                elif image and image.startswith('/'):
                    image = 'https://poizon.com' + image
        except Exception as e:
            print(f"❌ Ошибка получения фото из API: {e}")

    # --- 7. Картинка: только из галереи товара ---
    if not image:
        gallery_img = soup.select_one('.swiper img, .banner img, .preview img, .product-img img')
        src = gallery_img.get('src') if gallery_img else None
        print('gallery_img:', src)
        # Используем только если это фото из dewucdn.com/pro-img или dewucdn.com/detail-img
        if src and src.startswith('http') and ('dewucdn.com/pro-img' in src or 'dewucdn.com/detail-img' in src):
            image = src
        else:
            print('❗ Найдено нерелевантное изображение, пропускаем:', src)

    # --- 8. Если ничего не нашли — заглушка ---
    if not image:
        print('❗ Не найдено фото товара, используется заглушка')
        image = 'https://via.placeholder.com/300x300?text=Нет+изображения'

    print('Финальное фото для сайта:', image)

    return {
        'success': True if (name and price) else False,
        'product_name': name,
        'price_cny': round(float(price), 2) if price else None,
        'image_url': image,
        'resolved_url': resolved_url
    }


def analyze_html_structure(soup):
    """
    Анализ HTML-структуры страницы для поиска ключевых элементов.
    """
    print("🔍 Анализ HTML-структуры страницы...")

    # Поиск блоков с ценой
    price_elements = soup.find_all(class_=re.compile(r'price|cost|amount'))
    print("💰 Найденные элементы цены:")
    for elem in price_elements:
        print(elem.get_text(strip=True))

    # Поиск блоков с изображением
    image_elements = soup.find_all('img')
    print("🖼️ Найденные элементы изображения:")
    for elem in image_elements:
        print(elem.get('src'))

    # Поиск блоков с именем товара
    name_elements = soup.find_all(class_=re.compile(r'name|title|product'))
    print("📦 Найденные элементы имени товара:")
    for elem in name_elements:
        print(elem.get_text(strip=True))

    print("✅ Анализ завершён.")
    
@app.route('/get_product_info', methods=['POST'])
def get_product_info():
    url = request.json.get('url')
    if not url:
        return jsonify({'success': False, 'error': 'URL не указан'})

    try:
        session = HTMLSession()
        # Увеличиваем таймаут и добавляем стандартный User-Agent
        response = session.get(url, timeout=25, headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'})
        # Даем больше времени на рендеринг JS
        response.html.render(sleep=3, timeout=25)
        
        soup = BeautifulSoup(response.html.html, 'html.parser')

        if "dewu.com" in url or "poizon.com" in url:
            # --- Поиск названия ---
            product_name_element = soup.select_one('.detail-title_name')
            product_name = product_name_element.text.strip() if product_name_element else "Название не найдено"
            print(f"Найдено название: {product_name}")

            # --- Поиск изображения ---
            image_element = soup.select_one('.detail-banner-swiper_img')
            image_url = image_element['src'] if image_element and image_element.has_attr('src') else ""
            if image_url:
                 print(f"Найдено изображение: {image_url}")

            # --- Попытка получить цену через API для размера 42 ---
            preferred_size = '42'
            price_cny = None
            # Извлекаем ID товара из URL для API
            m = re.search(r'(?:spuId|itemId)[=|/](\d+)', url)
            if m:
                item_id = m.group(1)
                try:
                    api_url = f"https://app.dewu.com/api/v1/product/detail?spuId={item_id}"
                    resp = requests.get(api_url, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10, verify=False)
                    if resp.status_code == 200:
                        data = resp.json()
                        sku_list = data.get('data', {}).get('skuList', [])
                        for sku in sku_list:
                            size_val = sku.get('size') or sku.get('skuName') or sku.get('specValue') or ''
                            if str(size_val) == preferred_size and int(sku.get('stockNum', 0)) > 0:
                                sku_price = sku.get('skuPrice') or {}
                                price_fen = sku_price.get('actualSalePrice') or sku_price.get('salePrice')
                                if price_fen:
                                    price_cny = float(price_fen) / 100
                                    print(f"✅ Цена для размера {preferred_size}: {price_cny} CNY")
                                    break
                except Exception as e:
                    print(f"⚠️ Не удалось получить цену через API для размера {preferred_size}: {e}")

            # --- Улучшенный поиск цены ---
            if price_cny is None:
                price_cny = 0
                # 1. Основной селектор цены на странице
                price_element = soup.select_one('.ProductPrice--priceNum--3Y2a3')
                if price_element:
                    price_text = price_element.get_text(strip=True)
                    m = re.search(r'(\d[\d,.]*)', price_text)
                    if m:
                        price_cny = float(m.group(1).replace(',', ''))
                        print(f"Найдена цена по основному селектору: {price_cny} CNY")
                # 2. Альтернативные селекторы
                if price_cny == 0:
                    selectors = ['.Price--originNum--12aA5_0', '.Price--priceText--2n--w', '.product-price', '[class*="price"]']
                    for sel in selectors:
                        el = soup.select_one(sel)
                        if el:
                            txt = el.get_text(strip=True)
                            mm = re.search(r'(\d[\d,.]*)', txt)
                            if mm:
                                price_cny = float(mm.group(1).replace(',', ''))
                                print(f"Найдена цена по селектору '{sel}': {price_cny} CNY")
                                break
                # 3. Поиск в JSON-LD
                if price_cny == 0:
                    scripts = soup.find_all('script', type='application/ld+json')
                    for sc in scripts:
                        try:
                            data = json.loads(sc.string)
                            offers = data.get('offers', {})
                            if isinstance(offers, dict) and 'price' in offers:
                                price_cny = float(offers['price'])
                            elif isinstance(offers, list) and offers and 'price' in offers[0]:
                                price_cny = float(offers[0]['price'])
                            if price_cny:
                                print(f"Найдена цена в JSON-LD: {price_cny} CNY")
                                break
                        except (json.JSONDecodeError, KeyError, TypeError, IndexError):
                            continue
                # 4. Комплексный парсер как дополнительный резерв
                if price_cny == 0:
                    comp = extract_product_data_comprehensive(soup, response.html.html, url)
                    if comp and comp.get('price'):
                        price_cny = comp['price']
                        print(f"Найдена цена через комплексный парсер: {price_cny} CNY")
                # 5. Финальный резерв: берем первую встреченную цену на странице
                if price_cny == 0:
                    for tag in soup.find_all(['span', 'div']):
                        text = tag.get_text(strip=True)
                        m = re.search(r'[¥￥]?\s?([\d,]+(?:\.\d+)?)', text)
                        if m:
                            try:
                                val = float(m.group(1).replace(',', ''))
                                if val > 0:
                                    price_cny = val
                                    print(f"Найдена цена из первого ценового тега: {price_cny} CNY")
                                    break
                            except:
                                continue

            else:
                print(f"✅ Используем цену API для размера {preferred_size}: {price_cny} CNY")

            if price_cny == 0:
                 print("Не удалось найти цену товара.")
                 return jsonify({'success': False, 'error': 'Не удалось найти цену товара. Попробуйте другую ссылку или проверьте правильность URL.'})

            # Получаем настройки
            settings = get_settings()
            
            # Получаем категории доставки
            conn = sqlite3.connect('database.db')
            cursor = conn.cursor()
            cursor.execute('SELECT category_name, delivery_cost FROM delivery_categories ORDER BY id')
            delivery_categories = cursor.fetchall()
            conn.close()
            
            # Если категорий нет, используем значение по умолчанию
            if not delivery_categories:
                delivery_categories = [('Стандартная доставка', settings['delivery_cost'])]
            
            # Рассчитываем цену
            price_cny = price_cny
            price_rub = price_cny * settings['cny_rate']
            
            # Используем стоимость доставки из первой категории по умолчанию
            delivery_cost = delivery_categories[0][1]
            total_price = delivery_cost + price_rub
            
            return jsonify({
                'success': True,
                'product_name': product_name,
                'image_url': image_url,
                'price_cny': price_cny,
                'price_rub': round(price_rub, 2),
                'delivery_cost': delivery_cost,
                'delivery_categories': [{'name': cat[0], 'cost': cat[1]} for cat in delivery_categories],
                'total_price': round(total_price, 2),
                'cny_rate': settings['cny_rate'],
                'resolved_url': url
            })
        
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})
    
    return jsonify({'success': False, 'error': 'Неизвестная ошибка'})


if __name__ == '__main__':
    print('>>> Flask app starting...')
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=False)

    print("✅ Анализ завершён.")
    
if __name__ == '__main__':
    print('>>> Flask app starting...')
    init_db()
    app.run(debug=True, host='0.0.0.0', port=5000, threaded=False)


