import logging
import aiohttp
import datetime
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
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
    """
    Форматирует строку даты из '%Y-%m-%d' или '%Y-%m-%d %H:%M' в русский текст.
    """
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


# ── Оригинальная логика получения данных о стране (из рабочего кода) ────

async def get_country_info(country_code):
    """
    Получает информацию о стране по коду страны с сайта geonames.org.
    Возвращает словарь с населением и площадью.
    """
    url = f"http://api.geonames.org/countryInfoJSON?country={country_code}&username={GEONAMES_USERNAME}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logging.warning(f"Geonames countryInfo API вернул код {response.status}")
                    return None
                data = await response.json()
                if 'geonames' in data and len(data['geonames']) > 0:
                    country_data = data['geonames'][0]
                    population   = country_data.get('population')
                    area_in_sqkm = country_data.get('areaInSqKm')
                    return {
                        'population':   int(population)   if population   else None,
                        'area_in_sqkm': float(area_in_sqkm) if area_in_sqkm else None
                    }
                else:
                    logging.warning("Данные о стране не найдены")
                    return None
    except Exception as e:
        logging.error(f"Ошибка при получении данных о стране: {e}")
        return None


async def get_city_population(city_name):
    """
    Получает код страны города через Geocoding API OpenWeatherMap.
    Всегда возвращает tuple (population, country_code).
    """
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={city_name}&limit=1&appid={OPENWEATHERMAP_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logging.warning(f"Geocoding API вернул код {response.status}")
                    return None, None
                data = await response.json()
                if not data or not isinstance(data, list) or len(data) == 0:
                    logging.warning("Город не найден")
                    return None, None
                population   = data[0].get('population')
                country_code = data[0].get('country')
                return population, country_code
    except Exception as e:
        logging.error(f"Ошибка при получении населения города: {e}")
        return None, None


# ── Построение графика ───────────────────────────────────────────────────

def build_forecast_chart(temp_dates, temp_mins, temp_maxs):
    """Строит график температур. Возвращает PNG-байты или None."""
    if not temp_dates:
        return None

    fig = None
    try:
        df = pd.DataFrame({
            'Дата': pd.to_datetime(temp_dates),
            'Мин. температура': temp_mins,
            'Макс. температура': temp_maxs
        })

        sns.set_context("talk", font_scale=1.4)
        fig, ax = plt.subplots(figsize=(10, 6))

        sns.lineplot(x='Дата', y='Мин. температура', data=df,
                     label='Мин. температура', linewidth=3, color='blue', ax=ax)
        sns.lineplot(x='Дата', y='Макс. температура', data=df,
                     label='Макс. температура', linewidth=3, color='red', ax=ax)

        ax.set_title('Температуры', fontsize=20)
        ax.xaxis.set_tick_params(labelsize=14)
        ax.yaxis.set_tick_params(labelsize=14)
        plt.xticks(rotation=45)
        plt.legend(fontsize=16)
        plt.tight_layout()

        buf = io.BytesIO()
        plt.savefig(buf, format='png')
        buf.seek(0)
        return buf.read()
    except Exception as e:
        logging.error(f"Ошибка при построении графика: {e}")
        return None
    finally:
        if fig is not None:
            plt.close(fig)  # всегда закрываем — предотвращаем утечку памяти


# ── Основная функция погоды ──────────────────────────────────────────────

async def get_weather(city):
    base_url     = "http://api.weatherapi.com/v1"
    current_url  = f"{base_url}/current.json"
    forecast_url = f"{base_url}/forecast.json"

    params_current = {
        'key':  WEATHERAPI_KEY,
        'q':    city,
        'lang': 'ru'
    }
    # Бесплатный тариф WeatherAPI — максимум 3 дня
    params_forecast = {
        'key':  WEATHERAPI_KEY,
        'q':    city,
        'days': 3,
        'lang': 'ru'
    }

    try:
        async with aiohttp.ClientSession() as session:

            # Текущая погода
            async with session.get(current_url, params=params_current) as response:
                if response.status != 200:
                    return {'text': f"Не удалось получить погоду для города '{city}'. Проверьте название.", 'fig_bytes': None}
                data_current = await response.json()

            location_name     = data_current['location']['name']
            country_name_full = data_current['location'].get('country', '')
            temp_c            = data_current['current']['temp_c']
            condition_text    = data_current['current']['condition']['text']
            localtime_str     = data_current['location'].get('localtime')

            if localtime_str:
                formatted_date_full = format_date(localtime_str)
                date_part = localtime_str.split(' ')[0]
                is_today  = (date_part == datetime.datetime.now().strftime('%Y-%m-%d'))
            else:
                formatted_date_full = "Дата недоступна"
                is_today = False

            # Прогноз
            async with session.get(forecast_url, params=params_forecast) as response_forecast:
                if response_forecast.status != 200:
                    return {'text': f"Не удалось получить прогноз для города '{city}'.", 'fig_bytes': None}
                forecast_data = await response_forecast.json()

        # Парсим прогноз (вне сессии — данные уже получены)
        forecast_lines = []
        temp_dates, temp_mins, temp_maxs = [], [], []

        for day in forecast_data['forecast']['forecastday']:
            date_str_raw = day['date']
            date_obj_day = datetime.datetime.strptime(date_str_raw, '%Y-%m-%d').date()

            # Пропускаем прошедшие дни; сегодня включаем
            if date_obj_day < datetime.date.today():
                continue

            min_temp = int(round(day['day']['mintemp_c']))
            max_temp = int(round(day['day']['maxtemp_c']))
            desc     = day['day']['condition']['text']

            label = "Сегодня" if date_obj_day == datetime.date.today() else format_date(date_str_raw)
            forecast_lines.append(
                f"{label}: {desc}, от {min_temp}°C ночью до {max_temp}°C днем."
            )
            temp_dates.append(date_str_raw)
            temp_mins.append(min_temp)
            temp_maxs.append(max_temp)

        forecast_str = "\n".join(forecast_lines)

        # Данные о стране — оригинальная логика из рабочего кода
        population_city, country_code_iso2 = await get_city_population(city)

        country_info = None
        if country_code_iso2:
            country_info = await get_country_info(country_code_iso2)

        if country_info and country_info.get('population') is not None and country_info.get('area_in_sqkm') is not None:
            population_country     = f"{country_info['population']:,}"
            area_country           = f"{country_info['area_in_sqkm']:,} км²"
            population_country_str = f"\nНаселение страны: {population_country} чел.\nПлощадь страны: {area_country}"
        else:
            population_country_str = ""

        # Формируем текст
        today_line = (
            f"🌤 Погода в {location_name}, {country_name_full}{population_country_str}:\n"
            f"Дата: {formatted_date_full}\n"
            f"Температура: {int(round(temp_c))}°C.\n"
            f"Состояние: {condition_text}."
        )

        result_text = f"{today_line}\n\nПрогноз на ближайшие 2 дня:\n{forecast_str}"

        fig_bytes = build_forecast_chart(temp_dates, temp_mins, temp_maxs)

        return {'text': result_text, 'fig_bytes': fig_bytes}

    except Exception as e:
        logging.exception(f"Необработанная ошибка в get_weather: {e}")
        return {'text': f"Произошла ошибка: {e}", 'fig_bytes': None}


def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("Бот запущен")
    app.run_polling()


if __name__ == '__main__':
    main()
