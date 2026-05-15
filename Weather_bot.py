import logging
import aiohttp
import datetime
import matplotlib.pyplot as plt
import seaborn as sns
import io
import pandas as pd
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

# Ваш токен бота
TELEGRAM_TOKEN = '7616872764:AAGICaPNKQ-XonGb40L_ZY77CptpFCileMU'

# Ваш API-ключ OpenWeatherMap (для геокодирования)
OPENWEATHERMAP_API_KEY = 'e796391b4447547d3b742c19be464345'  # ✅ Этот ключ уже добавлен

# Ваш логин на geonames.org (нужно зарегистрироваться и получить логин)
GEONAMES_USERNAME = 'your_geonames_username'  # замените на ваш логин

# Включаем логирование
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

def format_date(date_str):
    """
    Форматирует строку даты из формата '%Y-%m-%d' или '%Y-%m-%d %H:%M' в читаемый на русском язык.
    """
    try:
        date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        try:
            date_obj = datetime.datetime.strptime(date_str, '%Y-%m-%d %H:%M')
        except ValueError:
            return date_str

    days_ru = {
        'Monday': 'Понедельник',
        'Tuesday': 'Вторник',
        'Wednesday': 'Среда',
        'Thursday': 'Четверг',
        'Friday': 'Пятница',
        'Saturday': 'Суббота',
        'Sunday': 'Воскресенье'
    }

    months_ru = {
        1: "января",
        2: "февраля",
        3: "марта",
        4: "апреля",
        5: "мая",
        6: "июня",
        7: "июля",
        8: "августа",
        9: "сентября",
        10:"октября",
        11:"ноября",
        12:"декабря"
    }

    weekday_en = date_obj.strftime('%A')
    weekday_ru = days_ru.get(weekday_en, weekday_en)

    day = date_obj.day
    month = months_ru.get(date_obj.month, str(date_obj.month))
    year = date_obj.year

    return f"{weekday_ru}, {day} {month} {year} г."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Отправьте мне название города, и я расскажу вам погоду."
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    city = update.message.text.strip()
    weather_info = await get_weather(city)
    
    # Отправляем текстовую информацию о погоде
    await update.message.reply_text(weather_info['text'])
    
    # Если есть график температуры в виде линии, отправляем его как фото
    if weather_info.get('fig_bytes'):
        fig_bytes = weather_info['fig_bytes']
        buf = io.BytesIO(fig_bytes)
        buf.seek(0)
        await update.message.reply_photo(photo=buf)

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
                    population = country_data.get('population')
                    area_in_sqkm = country_data.get('areaInSqKm')
                    return {
                        'population': int(population) if population else None,
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
    Получает население города через Geocoding API OpenWeatherMap.
    """
    url = f"http://api.openweathermap.org/geo/1.0/direct?q={city_name}&limit=1&appid={OPENWEATHERMAP_API_KEY}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as response:
                if response.status != 200:
                    logging.warning(f"Geocoding API вернул код {response.status}")
                    return None
                data = await response.json()
                if not data or not isinstance(data, list) or len(data) == 0:
                    logging.warning("Город не найден")
                    return None
                population = data[0].get("population")
                country_code = data[0].get("country")  # получаем код страны для дальнейшего запроса
                return population, country_code
    except Exception as e:
        logging.error(f"Ошибка при получении населения города: {e}")
        return None, None

async def get_weather(city):
    api_key_weatherapi = '32e8b8ddfefc4a8f973121209251007'
    base_url = "http://api.weatherapi.com/v1"
    
    current_url = f"{base_url}/current.json"
    forecast_url = f"{base_url}/forecast.json"

    params_current = {
        'key': api_key_weatherapi,
        'q': city,
        'lang': 'ru'
    }

    params_forecast = {
        'key': api_key_weatherapi,
        'q': city,
        'days': 5,
        'lang': 'ru'
    }

    try:
        async with aiohttp.ClientSession() as session:
            # Получение текущей погоды
            async with session.get(current_url, params=params_current) as response:
                if response.status != 200:
                    return {'text': f"Не удалось получить погоду для города '{city}'. Проверьте название."}
                data_current = await response.json()

            location_name = data_current['location']['name']
            country_name_full = data_current['location'].get('country', '')
            country_code_full = data_current['location'].get('country_code', '')  # например, RU
            
            temp_c = data_current['current']['temp_c']
            condition_text = data_current['current']['condition']['text']
            localtime_str = data_current['location'].get('localtime')  # например: "2023-10-05 14:00"
            
            if localtime_str:
                formatted_date_full = format_date(localtime_str)
                date_part = localtime_str.split(' ')[0]
                today_str = datetime.datetime.now().strftime('%Y-%m-%d')
                is_today = (date_part == today_str)
            else:
                formatted_date_full = "Дата недоступна"
                is_today = False

            # Получение прогноза на 5 дней
            async with session.get(forecast_url, params=params_forecast) as response_forecast:
                if response_forecast.status != 200:
                    return {'text': f"Не удалось получить прогноз для города '{city}'."}
                forecast_data = await response_forecast.json()

            forecast_lines = []
            days_seen = set()

            # Для построения линии температуры собираем данные за 5 дней
            temp_dates=[]
            temp_mins=[]
            temp_maxs=[]

            for day in forecast_data['forecast']['forecastday']:
                date_str_raw=day['date'] # формат YYYY-MM-DD
                date_obj_day=datetime.datetime.strptime(date_str_raw,'%Y-%m-%d').date()

                # пропускаем прошедшие даты и сегодня
                if date_obj_day <= datetime.date.today():
                    continue

                if len(days_seen)>=5:
                    break

                if date_obj_day not in days_seen:
                    days_seen.add(date_obj_day)
                    date_formatted=format_date(date_str_raw)
                    min_temp=int(round(day['day']['mintemp_c']))
                    max_temp=int(round(day['day']['maxtemp_c']))
                    desc=day['day']['condition']['text']
                    forecast_lines.append(
                        f"{date_formatted}: {desc}, от {min_temp}°C ночью до {max_temp}°C днем."
                    )
                    temp_dates.append(date_str_raw)
                    temp_mins.append(min_temp)
                    temp_maxs.append(max_temp)

            forecast_str="\n".join(forecast_lines)

            # Получаем население города и код страны для получения данных о стране
            population_city, country_code_iso2=await get_city_population(city)
            
            # Получаем данные о стране по коду (например ISO2 или ISO3). В API geonames есть поле countryCode.
            country_info=None
            if country_code_iso2:
                country_info=await get_country_info(country_code_iso2)

            # Формируем строку с населением страны (если есть)
            if country_info and country_info.get('population') is not None and country_info.get('area_in_sqkm') is not None:
                population_country=f"{country_info['population']:,}"
                area_country=f"{country_info['area_in_sqkm']:,} км²"
                population_country_str=f"\nНаселение страны: {population_country} чел.\nПлощадь страны: {area_country}"
            else:
                population_country_str=""

            # Формируем строку с сегодняшней погодой
            if is_today:
                today_line=(
                    f"🌤 Погода в {location_name}, {country_name_full}{population_country_str}:\n"
                    f"Дата: {formatted_date_full}\n"
                    f"Температура: {int(round(temp_c))}°C.\n"
                    f"Состояние: {condition_text}."
                )
            else:
                today_line=(
                    f"🌤 Погода в {location_name}, {country_name_full}{population_country_str}:\n"
                    f"Дата: {formatted_date_full}\n"
                    f"Температура: {int(round(temp_c))}°C\n"
                    f"Состояние: {condition_text}"
                )

            # Построение графика температур (тот же как раньше)
            fig, ax=plt.subplots(figsize=(10,6))
            
            if temp_dates:
                df=pd.DataFrame({
                    'Дата': pd.to_datetime(temp_dates),
                    'Мин. температура': temp_mins,
                    'Макс. температура': temp_maxs
                })

                # Увеличиваем шрифты и делаем линии жирными для лучшей читаемости
                sns.set_context("talk", font_scale=1.4)  # увеличенные шрифты
                
                sns.lineplot(
                    x='Дата', 
                    y='Мин. температура', 
                    data=df,
                    label='Мин. температура',
                    linewidth=3,
                    color='blue'
                )
                
                sns.lineplot(
                    x='Дата', 
                    y='Макс. температура', 
                    data=df,
                    label='Макс. температура',
                    linewidth=3,
                    color='red'
                )

                ax.set_title('Температуры за 5 дней', fontsize=20)
                
                # Сделать оси более крупными и читаемыми
                ax.xaxis.set_tick_params(labelsize=14)
                ax.yaxis.set_tick_params(labelsize=14)
                
                plt.xticks(rotation=45)
                
                plt.legend(fontsize=16)
                
                plt.tight_layout()

                buf=io.BytesIO()
                plt.savefig(buf, format='png')
                plt.close(fig)
                buf.seek(0)
                fig_bytes=buf.read()
            else:
                fig_bytes=None

            result_text=f"{today_line}\n\nКлиматический прогноз на пять дней:\n{forecast_str}"

            return {'text': result_text,'fig_bytes':fig_bytes}

    except Exception as e:
        return {'text': f"Произошла ошибка: {e}",'fig_bytes':None}

def main():
    app=ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    
     # Обработка текстовых сообщений (города)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

     # Запуск бота
    print("Бот запущен")
    app.run_polling()

if __name__=='__main__':
     main() 
