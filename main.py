import os
import requests
import urllib3
from aiohttp import web, ClientSession
from lxml import etree
from dotenv import load_dotenv

from log_manager import log_manager, setup_log_tasks

load_dotenv()

# === КОНФИГУРАЦИЯ (из .env) ===
BASE_SERVICE_URL = os.environ["BASE_SERVICE_URL"]
REG_USER_TOKEN = os.environ["REG_USER_TOKEN"]
SOAP_URL = os.environ["SOAP_URL"]
CAS_VALIDATE_URL = os.environ["CAS_VALIDATE_URL"]
ESIA_OBMEN_DIR = os.environ.get("ESIA_OBMEN_DIR")
HOST = os.environ.get("HOST", "0.0.0.0")
PORT = int(os.environ.get("PORT", "1100"))


# === ФУНКЦИЯ: ВЫЗОВ SOAP-СЕРВИСА ===
def get_user_from_soap_service(snils: str):
    # logging.info(f"Получен снилс для soap: {snils}") #
    snils_clean = "".join(filter(str.isdigit, snils))
    if len(snils_clean) != 11:
        log_manager.log_sync("ERROR", "Некорректный СНИЛС (длина не 11 цифр)")
        return None

    soap_body = f"""
    <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/"
                      xmlns:reg="http://www.hostco.ru/reguser"
                      xmlns:typ="http://www.hostco.ru/reguser/types">
       <soapenv:Header/>
       <soapenv:Body>
          <reg:getUserRequest typ:token="{REG_USER_TOKEN}">
             <typ:SNILS>{snils_clean}</typ:SNILS>
          </reg:getUserRequest>
       </soapenv:Body>
    </soapenv:Envelope>
    """

    headers = {
        "Content-Type": "text/xml; charset=utf-8",
        "SOAPAction": "http://www.hostco.ru/reguser/getUser"
    }

    # Отправка/прием SOAP
    try:
        response = requests.post(SOAP_URL
                                 , data=soap_body
                                 , headers=headers
                                 , timeout=10
                                 , verify=False
                                 )
        # logging.info(f"SOAP-статус: {response.status_code}") #

        if response.status_code == 200:
            # logging.info(f"полный SOAP XML: {response.text}") #
            return response.text  # ← это и есть полный XML
        else:
            log_manager.log_sync("ERROR", f"SOAP вернул ошибку: {response.status_code}")
            return None

    except requests.exceptions.ConnectionError:
        log_manager.log_sync("ERROR", "Не удалось подключиться к RegUserService")
        return None
    except requests.exceptions.Timeout:
        log_manager.log_sync("ERROR", "Таймаут при запросе к RegUserService")
        return None
    except etree.XMLSyntaxError:
        log_manager.log_sync("ERROR", "Ответ RegUserService не является валидным XML")
        # logging.error(f"Сырой ответ:\n{response.text}")  #
        return None
    except Exception:
        log_manager.log_sync("ERROR", "Ошибка при вызове SOAP RegUserService")
        return None

    return None


# === ФУНКЦИЯ: ОТПРАВКА ДАННЫХ В MAX-БОТА ===
async def send_to_max_bot(user_id: str, user_data: dict):
    await log_manager.log("INFO", "Запрос на отправку в Max-бот")

    dir_path = ESIA_OBMEN_DIR
    file_path = os.path.join(dir_path, f"{user_id}.txt")
    # timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # ФИО; телефон; дата рождения; СНИЛС; ОМС; пол
    snils = "null"
    lastname = "null"
    firstname = "null"
    middlename = "null"
    phone = "null"
    birth_date = "null"
    gender = "null"
    policynumber = "null"

    if user_data:
        try:
            # Парсим XML
            root = etree.fromstring(user_data.encode('utf-8'))
            ns = {'ns': 'http://www.hostco.ru/reguser/types'}

            # Извлекаем данные
            snils_elem = root.xpath('//ns:SNILS/text()', namespaces=ns)
            snils = snils_elem[0].strip() if snils_elem and snils_elem[0].strip() else "null"

            lastname_elem = root.xpath('//ns:Lastname/text()', namespaces=ns)
            lastname = lastname_elem[0].strip() if lastname_elem and lastname_elem[0].strip() else "null"

            firstname_elem = root.xpath('//ns:Firstname/text()', namespaces=ns)
            firstname = firstname_elem[0].strip() if firstname_elem and firstname_elem[0].strip() else "null"

            middlename_elem = root.xpath('//ns:Middlename/text()', namespaces=ns)
            middlename = middlename_elem[0].strip() if middlename_elem and middlename_elem[0].strip() else "null"

            phone_elem = root.xpath('//ns:Phone/text()', namespaces=ns)
            phone = phone_elem[0].strip() if phone_elem and phone_elem[0].strip() else "null"

            birth_date_elem = root.xpath('//ns:BirthDate/text()', namespaces=ns)
            birth_date = birth_date_elem[0].strip() if birth_date_elem and birth_date_elem[0].strip() else "null"

            gender_elem = root.xpath('//ns:gender/text()', namespaces=ns)
            gender = gender_elem[0].strip() if gender_elem and gender_elem[0].strip() else "null"

            policynumber_elem = root.xpath('//ns:PolicyNumber/text()', namespaces=ns)
            policynumber = policynumber_elem[0].strip() if policynumber_elem and policynumber_elem[
                0].strip() else "null"

        except Exception:
            await log_manager.log("ERROR", "Ошибка парсинга XML")

    line = f"{lastname} {firstname} {middlename},{phone},{birth_date},{snils},{policynumber},{gender}\n"
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(line)
        await log_manager.log("INFO", "Запись в папку esia_obmen выполнена")
        await log_manager.log_successful_write()
    except OSError:
        await log_manager.log("ERROR", "Ошибка записи в папку esia_obmen")
        await log_manager.log_unsuccessful_write()

    return None


# === ОСНОВНОЙ ОБРАБОТЧИК ===
async def handle_callback(request):
    # logging.info(f"Полный ответ от CAS: {request.url}") #
    ticket = request.query.get('ticket')  # Тикет возврашенный ЕСИА
    user_id = request.query.get('user_id')  # ID диалога в Максе переданного из бота через ЕСИА
    # logging.info(f"tiket: {ticket}") #
    # logging.info(f"user_id: {user_id}") #
    if not ticket or not user_id:
        await log_manager.log("WARNING", "Нет ticket или user_id")
        return web.Response(text="Нет ticket или user_id", status=400)
    # logging.info(f"Получен ticket: {ticket}, user_id: {user_id}")#

    # Шаг 1: Отправляем на валидацию ticket в CAS
    async with ClientSession() as session:
        validate_url = f"{CAS_VALIDATE_URL}?service={BASE_SERVICE_URL}?user_id={user_id}&ticket={ticket}"
        async with session.get(validate_url) as resp:
            xml_response = await resp.text()
            # logging.info(f"2. Полный ответ от CAS:\n{xml_response}")  #

    # Шаг 2: для вызова SOAP извлекаем СНИЛС из <cas:user>
    snils_for_soap = None
    try:
        import xml.etree.ElementTree as ET
        namespaces = {'cas': 'http://www.yale.edu/tp/cas'}
        root = ET.fromstring(xml_response)
        user_elem = root.find('.//cas:user', namespaces)

        if user_elem is not None and user_elem.text:
            raw_snils = user_elem.text.strip()
            snils_clean = "".join(filter(str.isdigit, raw_snils))
            if len(snils_clean) == 11:
                snils_for_soap = snils_clean
                # logging.info(f"Извлечён СНИЛС для SOAP: {snils_for_soap}") #
            else:
                await log_manager.log("WARNING", "Некорректная длина СНИЛС (не 11 цифр)")
        else:
            await log_manager.log("WARNING", "Элемент <cas:user> пуст или отсутствует")

    except Exception:
        await log_manager.log("ERROR", "Ошибка парсинга XML из CAS")

    # Шаг 3: Получение данных через SOAP
    soap_xml = None
    if snils_for_soap:
        soap_xml = get_user_from_soap_service(snils_for_soap)
        # logging.info(f"Получен снилс от soap: {soap_xml}") #
    else:
        await log_manager.log("WARNING", "СНИЛС не получен")

    # Шаг 4: Отправка в Max-бота
    await send_to_max_bot(user_id, soap_xml)

    # Ответ пользователю в браузере
    return web.Response(
        text="✅ Авторизация успешна! Вернитесь в бот Макс.",
        content_type='text/html'
    )


# === ЗАПУСК ===
app = web.Application()
app.router.add_get('/auth/cascallback', handle_callback)

setup_log_tasks(app)

if __name__ == '__main__':
    web.run_app(app, host=HOST, port=PORT)

