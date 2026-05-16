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
    return f"{weekday_ru}, {date_obj.day} {months_ru[date_obj.month]} {date_obj.year} г."


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправьте мне название города, и я расскажу вам погоду."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    weather_info = await get_weather(city)
    await update.message.reply_text(weather_info['text'])
    if weather_info.get('fig_bytes'):
        buf = io.BytesIO(weather_info['fig_bytes'])
        buf.seek(0)
        await update.message.reply_photo(photo=buf)


# ── Внешние API ───────────────────────────────────────────────────────────

async def geonames_search_city(session, city_name):
    """
    Ищет город через geonames и возвращает:
    population (город), country_code, population_country, area_sqkm,
    currency_code, currency_name.
    """
    url = 'http://api.geonames.org/searchJSON'
    params = {
        'q':            city_name,
        'maxRows':      5,
        'featureClass': 'P',
        'style':        'FULL',
        'username':     GEONAMES_USERNAME,
    }
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=8)) as resp:
            logging.info(f"geonames search статус: {resp.status}")
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            items = data.get('geonames', [])
            if not items:
                logging.warning(f"geonames search: город {city_name} не найден")
                return None
            # Берём город с наибольшим населением
            best = max(items, key=lambda x: int(x.get('population', 0) or 0))
            logging.info(f"geonames: найден {best.get('name')}, pop={best.get('population')}, country={best.get('countryCode')}")
            return {
                'city_population': int(best['population']) if best.get('population') else None,
                'country_code':    best.get('countryCode', ''),
            }
    except Exception as e:
        logging.error(f"geonames search ошибка: {e}")
        return None


async def geonames_country_info(session, country_code):
    """Возвращает население страны, площадь, код и название валюты."""
    url = 'http://api.geonames.org/countryInfoJSON'
    params = {'country': country_code, 'username': GEONAMES_USERNAME}
    try:
        async with session.get(url, params=params,
                               timeout=aiohttp.ClientTimeout(total=6)) as resp:
            logging.info(f"geonames countryInfo [{country_code}] статус: {resp.status}")
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            items = data.get('geonames', [])
            if not items:
                logging.warning(f"geonames countryInfo: нет данных для {country_code}")
                return None
            d = items[0]
            return {
                'population':    int(d['population'])     if d.get('population')   else None,
                'area_in_sqkm':  float(d['areaInSqKm'])   if d.get('areaInSqKm')   else None,
                'currency_code': d.get('currencyCode', ''),
                'currency_name': d.get('currencyName', ''),
            }
    except Exception as e:
        logging.error(f"geonames countryInfo ошибка: {e}")
        return None


async def get_currency_rate(session, currency_code):
    """Курс валюты к USD через open.er-api.com (бесплатно, без ключа)."""
    if not currency_code or currency_code.upper() == 'USD':
        return None
    url = 'https://open.er-api.com/v6/latest/USD'
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=6)) as resp:
            if resp.status != 200:
                return None
            data = await resp.json(content_type=None)
            rate = data.get('rates', {}).get(currency_code.upper())
            logging.info(f"Курс USD -> {currency_code}: {rate}")
            return float(rate) if rate else None
    except Exception as e:
        logging.error(f"ExchangeRate ошибка: {e}")
        return None


# ── График ────────────────────────────────────────────────────────────────

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
        logging.error(f"График ошибка: {e}")
        return None
    finally:
        if fig is not None:
            plt.close(fig)


# ── Основная функция ──────────────────────────────────────────────────────

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
                        'text': "Не удалось получить погоду для «" + city + "». Проверьте название.",
                        'fig_bytes': None
                    }
                data_current = await resp.json(content_type=None)

            location_name     = data_current['location']['name']
            country_name_full = data_current['location'].get('country', '')
            temp_c            = data_current['current']['temp_c']
            condition_text    = data_current['current']['condition']['text']
            localtime_str     = data_current['location'].get('localtime', '')
            formatted_date    = format_date(localtime_str) if localtime_str else 'Дата недоступна'

            # ── Прогноз ────────────────────────────────────────────────
            async with session.get(forecast_url, params=params_forecast,
                                   timeout=aiohttp.ClientTimeout(total=10)) as resp_f:
                if resp_f.status != 200:
                    return {
                        'text': "Не удалось получить прогноз для «" + city + "».",
                        'fig_bytes': None
                    }
                forecast_data = await resp_f.json(content_type=None)

            # ── Данные о городе и стране через Geonames ─────────────────
            city_info    = await geonames_search_city(session, location_name)
            country_info = None
            city_pop     = None
            country_code = None

            if city_info:
                city_pop     = city_info.get('city_population')
                country_code = city_info.get('country_code')
                if country_code:
                    country_info = await geonames_country_info(session, country_code)

            # ── Курс валюты ─────────────────────────────────────────────
            currency_rate = None
            currency_code_str = ''
            currency_name_str = ''
            if country_info:
                currency_code_str = country_info.get('currency_code', '')
                currency_name_str = country_info.get('currency_name', '')
                if currency_code_str and currency_code_str != 'USD':
                    currency_rate = await get_currency_rate(session, currency_code_str)

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

            # ── Строки с данными ────────────────────────────────────────
            logging.info("city_info=%s city_pop=%s country_code=%s country_info=%s",
                         city_info, city_pop, country_code, country_info)

            city_pop_str = ''
            if city_pop:
                city_pop_str = '\nНаселение города: ' + '{:,}'.format(city_pop) + ' чел.'

            country_str = ''
            if country_info and country_info.get('population') and country_info.get('area_in_sqkm'):
                country_str = (
                    '\nНаселение страны: ' + '{:,}'.format(country_info['population']) + ' чел.'
                    + '\nПлощадь страны: ' + '{:,}'.format(country_info['area_in_sqkm']) + ' км²'
                )

            currency_str = ''
            if currency_rate and currency_code_str:
                currency_str = '\n💱 1 USD = ' + '{:.2f}'.format(currency_rate) + ' ' + currency_code_str
                if currency_name_str:
                    currency_str += ' (' + currency_name_str + ')'

            # ── Итоговый текст ──────────────────────────────────────────
            today_line = (
                '🌤 Погода в ' + location_name + ', ' + country_name_full
                + city_pop_str
                + country_str
                + currency_str + ':\n'
                + 'Дата: ' + formatted_date + '\n'
                + 'Температура: ' + str(int(round(temp_c))) + '°C.\n'
                + 'Состояние: ' + condition_text + '.'
            )

            forecast_str = '\n'.join(forecast_lines)
            result_text  = today_line + '\n\nПрогноз на ближайшие 2 дня:\n' + forecast_str

            fig_bytes = build_forecast_chart(hourly_times, hourly_temps, day_labels)

            return {'text': result_text, 'fig_bytes': fig_bytes}

    except Exception as e:
        logging.exception("Необработанная ошибка в get_weather: " + str(e))
        return {'text': 'Произошла ошибка: ' + str(e), 'fig_bytes': None}


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен")
    app.run_polling()


if __name__ == '__main__':
    main()
