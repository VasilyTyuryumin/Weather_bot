import logging
import aiohttp
import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns
import io
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# ── Ключи и токены ──────────────────────────────────────────────────────
TELEGRAM_TOKEN         = '7616872764:AAGICaPNKQ-XonGb40L_ZY77CptpFCileMU'
OPENWEATHERMAP_API_KEY = 'e796391b4447547d3b742c19be464345'
WEATHERAPI_KEY         = '32e8b8ddfefc4a8f973121209251007'
GEONAMES_USERNAME      = 'v1331v'

# ── Логирование ─────────────────────────────────────────────────────────
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)


def format_date(date_str):
    """Форматирует '%Y-%m-%d' или '%Y-%m-%d %H:%M' в русский текст."""
    try:
        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        try:
            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M')
        except ValueError:
            return date_str
    days_ru = {
        'Monday': 'Понедельник', 'Tuesday': 'Вторник', 'Wednesday': 'Среда',
        'Thursday': 'Четверг', 'Friday': 'Пятница',
        'Saturday': 'Суббота', 'Sunday': 'Воскресенье'
    }
    months_ru = {
        1: 'января', 2: 'февраля', 3: 'марта', 4: 'апреля',
        5: 'мая', 6: 'июня', 7: 'июля', 8: 'августа',
        9: 'сентября', 10: 'октября', 11: 'ноября', 12: 'декабря'
    }
    weekday_ru = days_ru.get(date_obj.strftime('%A'), date_obj.strftime('%A'))
    return weekday_ru + ', ' + str(date_obj.day) + ' ' + months_ru[date_obj.month] + ' ' + str(date_obj.year) + ' г.'


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправьте мне название города, и я расскажу вам погоду и полезную информацию о нём."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    weather_info = await get_weather(city)
    await update.message.reply_text(weather_info['text'])
    if weather_info.get('fig_bytes'):
        buf = io.BytesIO(weather_info['fig_bytes'])
        buf.seek(0)
        await update.message.reply_photo(photo=buf)


# ════════════════════════════════════════════════════════════════════════
# API-функции
# ════════════════════════════════════════════════════════════════════════

async def geonames_search_city(session, city_name):
    """Ищет город через geonames. Возвращает население города и код страны."""
    url = 'http://api.geonames.org/searchJSON'
    params = {
        'q': city_name, 'maxRows': 10,
        'featureClass': 'P', 'style': 'FULL',
        'username': GEONAMES_USERNAME,
    }
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=8)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            items = data.get('geonames', [])
            if not items:
                return None

            def safe_pop(x):
                try:
                    return int(x.get('population') or 0)
                except (ValueError, TypeError):
                    return 0

            best = max(items, key=safe_pop)
            pop_val = safe_pop(best)
            logging.info('geonames city: %s pop=%s country=%s',
                         best.get('name'), pop_val, best.get('countryCode'))
            return {
                'city_population': pop_val if pop_val > 0 else None,
                'country_code':    best.get('countryCode', ''),
                'lat':             best.get('lat'),
                'lng':             best.get('lng'),
            }
    except Exception as e:
        logging.error('geonames search: %s', e)
        return None


async def geonames_country_info(session, country_code):
    """Население и площадь страны, код и название валюты через geonames."""
    url = 'http://api.geonames.org/countryInfoJSON'
    params = {'country': country_code, 'username': GEONAMES_USERNAME}
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            items = data.get('geonames', [])
            if not items:
                return None
            d = items[0]
            return {
                'population':    int(d['population'])     if d.get('population')   else None,
                'area_in_sqkm':  float(d['areaInSqKm'])   if d.get('areaInSqKm')   else None,
                'currency_code': d.get('currencyCode', ''),
                'currency_name': d.get('currencyName', ''),
            }
    except Exception as e:
        logging.error('geonames countryInfo: %s', e)
        return None


async def restcountries_info(session, country_code):
    """
    Получает через restcountries.com:
    столица, языки, телефонный код, домен, сторона движения.
    """
    url = 'https://restcountries.com/v3.1/alpha/' + country_code
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            if not data or not isinstance(data, list):
                return None
            d = data[0]

            # Столица
            capital = ''
            capitals = d.get('capital', [])
            if capitals:
                capital = capitals[0]

            # Официальные языки
            languages = d.get('languages', {})
            lang_list = ', '.join(languages.values()) if languages else ''

            # Телефонный код
            idd = d.get('idd', {})
            phone_root   = idd.get('root', '')
            phone_suffix = idd.get('suffixes', [''])[0] if idd.get('suffixes') else ''
            phone_code   = phone_root + phone_suffix

            # Домен
            tld_list = d.get('tld', [])
            tld = tld_list[0] if tld_list else ''

            # Сторона движения
            car = d.get('car', {})
            side_en = car.get('side', '')
            side_ru = 'правостороннее' if side_en == 'right' else 'левостороннее' if side_en == 'left' else ''

            logging.info('restcountries: capital=%s lang=%s phone=%s tld=%s side=%s',
                         capital, lang_list, phone_code, tld, side_ru)
            return {
                'capital':    capital,
                'languages':  lang_list,
                'phone_code': phone_code,
                'tld':        tld,
                'side':       side_ru,
            }
    except Exception as e:
        logging.error('restcountries: %s', e)
        return None


async def get_timezone_info(session, city_name, country_code):
    """
    Часовой пояс и местное время через worldtimeapi.org.
    Пробует по городу, затем по стране.
    """
    # Пробуем найти через worldtimeapi список зон
    try:
        url = 'https://worldtimeapi.org/api/timezone'
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status == 200:
                zones = await resp.json(content_type=None)
                # Ищем зону содержащую название города
                city_lower = city_name.lower().replace(' ', '_')
                matched = None
                for z in zones:
                    if city_lower in z.lower():
                        matched = z
                        break
                # Если не нашли по городу — берём первую зону страны
                if not matched and country_code:
                    for z in zones:
                        parts = z.split('/')
                        if len(parts) >= 2 and parts[0] in ('Europe', 'Asia', 'America',
                                                              'Africa', 'Pacific', 'Atlantic',
                                                              'Indian', 'Australia'):
                            pass  # ищем по коду страны ниже

                if matched:
                    url2 = 'https://worldtimeapi.org/api/timezone/' + matched
                    async with session.get(url2, timeout=aiohttp.ClientTimeout(total=5)) as r2:
                        if r2.status == 200:
                            d2 = await r2.json(content_type=None)
                            tz       = d2.get('timezone', '')
                            dt_str   = d2.get('datetime', '')
                            utc_off  = d2.get('utc_offset', '')
                            local_time = ''
                            if dt_str:
                                try:
                                    local_time = dt_str[11:16]  # HH:MM
                                except Exception:
                                    pass
                            return {'timezone': tz, 'local_time': local_time, 'utc_offset': utc_off}
    except Exception as e:
        logging.warning('worldtimeapi: %s', e)
    return None


async def get_nearest_airport(session, lat, lng):
    """
    Ближайший аэропорт через airport-data.com или через geonames nearbyJSON.
    Используем geonames nearbyJSON с featureCode=AIRP.
    """
    if not lat or not lng:
        return None
    url = 'http://api.geonames.org/findNearbyJSON'
    params = {
        'lat':         lat,
        'lng':         lng,
        'featureCode': 'AIRP',
        'maxRows':     1,
        'username':    GEONAMES_USERNAME,
    }
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            items = data.get('geonames', [])
            if not items:
                return None
            airport = items[0]
            name = airport.get('name', '')
            dist = airport.get('distance', '')
            logging.info('Ближайший аэропорт: %s, %s км', name, dist)
            return {'name': name, 'distance': dist}
    except Exception as e:
        logging.error('airport geonames: %s', e)
        return None


async def get_holidays(session, country_code):
    """Праздники страны сегодня через date.nager.at."""
    today = datetime.date.today()
    url = 'https://date.nager.at/api/v3/PublicHolidays/' + str(today.year) + '/' + country_code
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            if not isinstance(data, list):
                return None
            today_str = today.strftime('%Y-%m-%d')
            holidays_today = [h.get('localName') or h.get('name')
                              for h in data if h.get('date') == today_str]
            logging.info('Праздники сегодня: %s', holidays_today)
            return holidays_today if holidays_today else None
    except Exception as e:
        logging.error('date.nager.at: %s', e)
        return None


async def get_currency_rate(session, currency_code):
    """Курс валюты к USD. Пробует open.er-api.com, затем frankfurter.app."""
    if not currency_code or currency_code.upper() == 'USD':
        return None
    code = currency_code.upper()
    try:
        url = 'https://open.er-api.com/v6/latest/USD'
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status == 200:
                data = await resp.json(content_type=None)
                rate = data.get('rates', {}).get(code)
                if rate:
                    return float(rate)
    except Exception as e:
        logging.warning('open.er-api: %s', e)
    try:
        url2 = 'https://api.frankfurter.app/latest?from=USD&to=' + code
        async with session.get(url2, timeout=aiohttp.ClientTimeout(total=6)) as resp2:
            if resp2.status == 200:
                data2 = await resp2.json(content_type=None)
                rate2 = data2.get('rates', {}).get(code)
                if rate2:
                    return float(rate2)
    except Exception as e:
        logging.warning('frankfurter: %s', e)
    return None


# ════════════════════════════════════════════════════════════════════════
# График
# ════════════════════════════════════════════════════════════════════════

def build_forecast_chart(hourly_times, hourly_temps, day_labels):
    """Почасовой график температуры. Возвращает PNG-байты или None."""
    if not hourly_times or not hourly_temps:
        return None
    fig = None
    try:
        sns.set_context("talk", font_scale=1.2)
        fig, ax = plt.subplots(figsize=(12, 6))

        ax.plot(hourly_times, hourly_temps, color='royalblue', linewidth=2.5,
                marker='o', markersize=4, label='Температура')
        ax.fill_between(hourly_times, hourly_temps,
                        min(hourly_temps) - 2, alpha=0.15, color='royalblue')

        ylim = ax.get_ylim()
        for day_date, day_label in day_labels:
            midnight = datetime.datetime.combine(day_date, datetime.time(0, 0))
            if hourly_times[0] < midnight < hourly_times[-1]:
                ax.axvline(x=midnight, color='gray', linestyle='--', linewidth=1, alpha=0.7)
                ax.text(midnight, ylim[1], day_label,
                        fontsize=10, color='gray', ha='center', va='bottom')

        for t, temp in zip(hourly_times, hourly_temps):
            if t.hour % 6 == 0:
                ax.annotate(str(int(round(temp))) + '°', xy=(t, temp),
                            xytext=(0, 8), textcoords='offset points',
                            ha='center', fontsize=9, color='navy')

        ax.set_title('Динамика температуры на 2 дня вперёд', fontsize=16, pad=12)
        ax.set_xlabel('Время', fontsize=13)
        ax.set_ylabel('Температура (°C)', fontsize=13)
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%d %b\n%H:%M'))
        ax.xaxis.set_major_locator(mdates.HourLocator(interval=6))
        ax.tick_params(axis='x', labelsize=10, rotation=30)
        ax.tick_params(axis='y', labelsize=11)
        ax.legend(fontsize=13)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png', dpi=120)
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logging.error('График: %s', e)
        return None
    finally:
        if fig is not None:
            plt.close(fig)


# ════════════════════════════════════════════════════════════════════════
# Основная функция
# ════════════════════════════════════════════════════════════════════════

async def get_weather(city):
    base_url     = 'http://api.weatherapi.com/v1'
    current_url  = base_url + '/current.json'
    forecast_url = base_url + '/forecast.json'

    params_current  = {'key': WEATHERAPI_KEY, 'q': city, 'lang': 'ru'}
    params_forecast = {'key': WEATHERAPI_KEY, 'q': city, 'days': 3, 'lang': 'ru'}

    try:
        async with aiohttp.ClientSession() as session:

            # ── Погода сейчас ───────────────────────────────────────────
            async with session.get(current_url, params=params_current,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    return {
                        'text': 'Не удалось получить погоду для «' + city + '». Проверьте название.',
                        'fig_bytes': None
                    }
                data_current = await resp.json(content_type=None)

            location_name     = data_current['location']['name']
            country_name_full = data_current['location'].get('country', '')
            temp_c            = data_current['current']['temp_c']
            condition_text    = data_current['current']['condition']['text']
            localtime_str     = data_current['location'].get('localtime', '')
            formatted_date    = format_date(localtime_str) if localtime_str else 'Дата недоступна'
            # Местное время из WeatherAPI (самый надёжный источник)
            local_time_wa = localtime_str[11:16] if len(localtime_str) >= 16 else ''
            timezone_wa   = data_current['location'].get('tz_id', '')

            # ── Прогноз ────────────────────────────────────────────────
            async with session.get(forecast_url, params=params_forecast,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp_f:
                if resp_f.status != 200:
                    return {
                        'text': 'Не удалось получить прогноз для «' + city + '».',
                        'fig_bytes': None
                    }
                forecast_data = await resp_f.json(content_type=None)

            # ── Geonames: город ─────────────────────────────────────────
            city_info    = await geonames_search_city(session, location_name)
            city_pop     = None
            country_code = None
            lat          = None
            lng          = None
            if city_info:
                city_pop     = city_info.get('city_population')
                country_code = city_info.get('country_code')
                lat          = city_info.get('lat')
                lng          = city_info.get('lng')

            # ── Geonames: страна ────────────────────────────────────────
            country_info = None
            if country_code:
                country_info = await geonames_country_info(session, country_code)

            # ── RestCountries: доп. данные о стране ─────────────────────
            rc_info = None
            if country_code:
                rc_info = await restcountries_info(session, country_code)

            # ── Курс валюты ─────────────────────────────────────────────
            currency_rate     = None
            currency_code_str = ''
            currency_name_str = ''
            if country_info:
                currency_code_str = country_info.get('currency_code', '')
                currency_name_str = country_info.get('currency_name', '')
                if currency_code_str and currency_code_str != 'USD':
                    currency_rate = await get_currency_rate(session, currency_code_str)

            # ── Ближайший аэропорт ──────────────────────────────────────
            airport_info = await get_nearest_airport(session, lat, lng)

            # ── Праздники сегодня ───────────────────────────────────────
            holidays = None
            if country_code:
                holidays = await get_holidays(session, country_code)

            # ── Парсим прогноз ──────────────────────────────────────────
            forecast_lines = []
            hourly_times   = []
            hourly_temps   = []
            day_labels     = []
            today          = datetime.date.today()

            for day in forecast_data['forecast']['forecastday']:
                date_str_raw = day['date']
                date_obj_day = datetime.datetime.strptime(date_str_raw, '%Y-%m-%d').date()
                if date_obj_day < today:
                    continue
                min_temp = int(round(day['day']['mintemp_c']))
                max_temp = int(round(day['day']['maxtemp_c']))
                desc     = day['day']['condition']['text']
                label    = 'Сегодня' if date_obj_day == today else format_date(date_str_raw)
                forecast_lines.append(
                    label + ': ' + desc + ', от ' + str(min_temp) + '°C ночью до ' + str(max_temp) + '°C днём.'
                )
                if 'hour' in day:
                    if date_obj_day != today:
                        day_labels.append((date_obj_day, label))
                    for h in day['hour']:
                        try:
                            dt = datetime.datetime.strptime(h['time'], '%Y-%m-%d %H:%M')
                            hourly_times.append(dt)
                            hourly_temps.append(h['temp_c'])
                        except Exception:
                            continue

            # ════════════════════════════════════════════════════════════
            # Собираем итоговый текст
            # ════════════════════════════════════════════════════════════

            lines = []
            lines.append('🌤 Погода в ' + location_name + ', ' + country_name_full)

            # Население города
            if city_pop:
                lines.append('👥 Население города: ' + '{:,}'.format(city_pop) + ' чел.')

            # Население и площадь страны
            if country_info:
                if country_info.get('population'):
                    lines.append('🌍 Население страны: ' + '{:,}'.format(country_info['population']) + ' чел.')
                if country_info.get('area_in_sqkm'):
                    lines.append('📐 Площадь страны: ' + '{:,}'.format(country_info['area_in_sqkm']) + ' км²')

            # Столица
            if rc_info and rc_info.get('capital'):
                lines.append('🏛️ Столица: ' + rc_info['capital'])

            # Официальные языки
            if rc_info and rc_info.get('languages'):
                lines.append('🗣️ Языки: ' + rc_info['languages'])

            # Телефонный код
            if rc_info and rc_info.get('phone_code'):
                lines.append('📞 Тел. код: ' + rc_info['phone_code'])

            # Домен страны
            if rc_info and rc_info.get('tld'):
                lines.append('🌐 Домен: ' + rc_info['tld'])

            # Сторона движения
            if rc_info and rc_info.get('side'):
                lines.append('🚗 Движение: ' + rc_info['side'])

            # Курс валюты
            if currency_rate and currency_code_str:
                rate_str = '💱 1 USD = ' + '{:.2f}'.format(currency_rate) + ' ' + currency_code_str
                if currency_name_str:
                    rate_str += ' (' + currency_name_str + ')'
                lines.append(rate_str)

            # Часовой пояс и местное время (из WeatherAPI — самый надёжный)
            if local_time_wa:
                tz_str = '🕐 Местное время: ' + local_time_wa
                if timezone_wa:
                    tz_str += ' (' + timezone_wa + ')'
                lines.append(tz_str)

            # Ближайший аэропорт
            if airport_info and airport_info.get('name'):
                airport_str = '✈️ Ближ. аэропорт: ' + airport_info['name']
                if airport_info.get('distance'):
                    airport_str += ' (' + str(airport_info['distance']) + ' км)'
                lines.append(airport_str)

            # Праздники сегодня
            if holidays:
                lines.append('🎉 Праздник сегодня: ' + ', '.join(holidays))

            # Пустая строка-разделитель
            lines.append('')

            # Погода
            lines.append('Дата: ' + formatted_date)
            lines.append('Температура: ' + str(int(round(temp_c))) + '°C.')
            lines.append('Состояние: ' + condition_text + '.')
            lines.append('')
            lines.append('Прогноз на ближайшие 2 дня:')
            lines.extend(forecast_lines)

            result_text = '\n'.join(lines)
            fig_bytes   = build_forecast_chart(hourly_times, hourly_temps, day_labels)

            return {'text': result_text, 'fig_bytes': fig_bytes}

    except Exception as e:
        logging.exception('Необработанная ошибка: ' + str(e))
        return {'text': 'Произошла ошибка: ' + str(e), 'fig_bytes': None}


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен")
    app.run_polling()


if __name__ == '__main__':
    main()
