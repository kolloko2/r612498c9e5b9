"""Authored DDS continuation of the 96 source calls, not official response rules.

Empty address components mean absent/ambiguous in the booklet, never Moscow by
default. The full source location remains on every card. Operational outcomes are
explicitly authored simulation facts, separate from the caller's known_facts.
"""
import re
from ticket_annotations import ANNOTATED, CHOICES


# ticket-call | response family | region | locality | street | house
# Each row was transcribed against tickets_source.json, including clarifications.
ROWS = """
01-1|fire|Москва|Москва||
01-2|police|Москва|Москва|Леонтьевский переулок|16
01-3|medical|Волгоградская область|Волжский|Карла Маркса|
02-1|smoke|Москва|Москва|Берзарина|21
02-2|police|Москва|Москва|Сущевский Вал|5
02-3|water|Москва|Москва||
03-1|fire|Московская область|Королёв|Станционная|28
03-2|police|||Бульвар Маршала Рокоссовского|25
03-3|rescue|||Симферопольское шоссе|
04-1|fire|Москва|Москва|Грина|11
04-2|medical|Московская область|Балашиха|Мирской проезд|16
04-3|access|Москва|Зеленоград||
05-1|fire|Москва|Москва|Цюрупы|12
05-2|medical|Москва|Москва|Коломенская набережная|18
05-3|rescue|Московская область|Подрезково||
06-1|fire|||МКАД|
06-2|medical|Москва|Москва|Красный Казанец|19Б
06-3|water|Московская область|Михнево||
07-1|fire|Москва|Москва|Декабристов|
07-2|medical||Рязань|Вишневая|15
07-3|search_police|Москва|Москва|Маршала Соколовского|1
08-1|smoke|Москва|Москва|Кировоградская|15
08-2|medical||||
08-3|water|Москва|Москва||
09-1|smoke|Москва|Москва||
09-2|medical||||
09-3|water|Москва|Москва||
10-1|fire|Москва|Москва||
10-2|medical||||
10-3|access|Москва|Москва|Проспект Маршала Жукова|20
11-1|fire|Москва|Москва|Дмитровское шоссе|110
11-2|medical|Москва|Москва|Беломорская|10
11-3|search|Владимирская область|Барыкино||
12-1|smoke|Московская область|Жуковка|Рублево-Успенское шоссе|70
12-2|medical|Тульская область|||
12-3|rescue|||МКАД|
13-1|fire|Москва|Москва||
13-2|medical||||
13-3|medical|||МКАД|
14-1|fire|Московская область||трасса М-2|
14-2|medical|Москва|Москва|Ленинградское шоссе|112
14-3|search_police|Москва|Москва|Комсомольская площадь|2А
15-1|fire|Московская область|Варварино||
15-2|medical|Московская область|Сколково||17
15-3|police|Москва|Москва|Зверенецкая|22
16-1|smoke|Московская область|||
16-2|medical|Волгоградская область|Волжский|Карла Маркса|
16-3|medical|Москва|Москва|Знаменские Садки|7
17-1|alarm|Москва|Москва|Большой Сухаревский переулок|19
17-2|medical|Москва|Москва|Твардовского|
17-3|police|Москва|Москва|Дмитровское шоссе|155
18-1|smoke||||
18-2|medical|Московская область|Красногорск|Пионерская|19
18-3|rescue|Москва|Москва|Огородный проезд|12
19-1|fire|Тульская область|||
19-2|medical|Рязанская область|||
19-3|medical||||
20-1|police|Москва|Москва|Олонецкий проезд|4
20-2|medical||||
20-3|police|Москва|Москва|Кастанаевская|42
21-1|police|Москва|Москва|Леонтьевский переулок|16
21-2|medical||||
21-3|access||||
22-1|police|Москва|Москва|Маршала Василевского|13
22-2|medical|||Ленинградское шоссе|
22-3|access|Москва|Москва|Белозерская|11Б
23-1|police|Москва|Москва|Тихомирова|15
23-2|medical|Москва|Зеленоград|Лесная|5
23-3|search_police|Москва|Москва|Домодедовская|34
24-1|police|Москва|Москва|Вешняковская|37
24-2|medical|Москва|Москва|Погонный проезд|1
24-3|police|Москва|Москва|Красная Пресня|
25-1|police|Москва|Москва|Фабрициуса|18
25-2|traffic|Москва|Москва|МКАД|
25-3|death|Московская область|Домодедлво|Текстильщиков|31
26-1|police|Москва|Москва|Ходынский бульвар|3
26-2|traffic|Москва|Москва|МКАД|
26-3|death|Москва|Москва|Лескова|6Б
27-1|suspicious|Москва|Москва|Тихорецкий бульвар|12
27-2|traffic|Москва|Москва|Садовое кольцо|
27-3|utility|Москва|Москва|Ленинский проспект|57
28-1|police|Москва|Москва|Матвеевская|28
28-2|traffic|Москва|Москва|Садовое кольцо|
28-3|utility|Москва|Москва|Комсомольская площадь|5
29-1|suspicious|Москва|Москва||
29-2|traffic|Тульская область|||
29-3|suspicious|Москва|Москва|проезд Шокальского|67
30-1|search_police|Москва|Москва|Ташкентская|25
30-2|traffic|Москва|Москва|Тюменская|
30-3|gas|Москва|ЛМС||20
31-1|search_police|Москва|Москва|Большая Ордынка|
31-2|traffic|Москва|Москва|Рублевское шоссе|
31-3|gas|Москва|Москва|Вавилова|81
32-1|search_police|Москва|Москва|Маргелова|
32-2|traffic|Москва|Москва|Волгоградский проспект|
32-3|lighting|||МКАД|
""".strip()

# family: owner, category, profile, work report, final report, assessed result fact
FAMILIES = {
    'fire': ('Служба 101', 'fire', 'fire', 'Проводим тушение и проверку опасной зоны.', 'Возгорание ликвидировано. Проверка опасной зоны завершена.', 'Возгорание ликвидировано'),
    'smoke': ('Служба 101', 'fire', 'fire', 'Проводим обследование места сообщения и поиск источника дыма.', 'Обследование завершено. Источник дыма устранён, дальнейшее реагирование нашей группы не требуется.', 'Обследование завершено'),
    'alarm': ('Служба 101', 'fire', 'fire', 'Проверяем помещения после срабатывания сигнализации.', 'Проверка завершена. Признаков пожара не обнаружено. Информация передана ответственному за здание.', 'Проверка завершена'),
    'medical': ('Служба 103', 'medical', 'medical', 'Приступили к осмотру пациента и оказанию помощи.', 'Помощь оказана. Пациент передан медицинским работникам для дальнейшего наблюдения. Работа бригады по этой карточке завершена.', 'Помощь оказана'),
    'police': ('Служба 102', 'public', 'police', 'Проводим проверку сообщения, устанавливаем обстоятельства и участников.', 'Проверка на месте завершена. Материалы переданы дежурному для дальнейшего рассмотрения.', 'Материалы переданы дежурному'),
    'search_police': ('Служба 102', 'public', 'police', 'Уточняем приметы и направление движения, передаём ориентировку дежурному.', 'Первичные мероприятия на месте завершены. Ориентировка передана дежурному, дальнейший розыск продолжается. Работа этого наряда на месте завершена.', 'Ориентировка передана'),
    'death': ('Служба 102', 'public', 'police', 'Обеспечиваем сохранность места и передаём сведения дежурному.', 'Место передано следственно-оперативной группе. Работа направленного наряда завершена.', 'Место передано'),
    'suspicious': ('Служба 102', 'public', 'police', 'Ограничили доступ в опасную зону. Ожидаем специалистов, сведения переданы дежурному.', 'Проверка завершена специалистами. Ограничение доступа снято по их указанию, работа наряда завершена.', 'Проверка завершена'),
    'traffic': ('Служба 102', 'traffic', 'police', 'Обозначили место происшествия, устанавливаем обстоятельства и обеспечиваем безопасность движения.', 'Оформление на месте завершено. Проезжая часть освобождена, движение восстановлено.', 'Движение восстановлено'),
    'water': ('Служба 101', 'other', 'general', 'Приступили к спасательным работам на воде.', 'Спасательные работы завершены. Люди переданы медицинским работникам, опасная зона проверена.', 'Спасательные работы завершены'),
    'rescue': ('Служба 101', 'other', 'general', 'Проводим обследование места и спасательные работы.', 'Спасательные работы завершены. Доступ к пострадавшим обеспечен, сведения переданы дежурному.', 'Спасательные работы завершены'),
    'access': ('Служба 101', 'other', 'general', 'Приступили к обеспечению доступа и проверке состояния людей.', 'Доступ обеспечен. Люди переданы профильной службе. Работы нашей группы завершены.', 'Доступ обеспечен'),
    'search': ('Служба 101', 'other', 'general', 'Установили связь с заявителем, начали поиск по переданным ориентирам.', 'Заявитель найден и выведен к дороге. Поисковые работы завершены.', 'Заявитель найден'),
    'utility': ('Деп. ЖКХ', 'utilities', 'utilities', 'Оградили опасный участок, приступили к обследованию повреждения.', 'Опасный участок ограждён. Результаты обследования переданы эксплуатирующей организации, первичные работы завершены.', 'Участок ограждён'),
    'gas': ('Служба 104', 'utilities', 'gas', 'Проводим обследование газового оборудования и локализацию неисправности.', 'Подача газа отключена на повреждённом участке. Опасность локализована, сведения переданы дежурному.', 'Подача газа отключена'),
    'lighting': ('Деп. ЖКХ', 'utilities', 'utilities', 'Проверяем режим работы уличного освещения на указанном участке.', 'Режим освещения скорректирован после проверки. Информация передана эксплуатирующей организации.', 'Режим освещения скорректирован'),
}


def _rows():
    result = {}
    for row in ROWS.splitlines():
        key, family, region, city, street, house = row.split('|')
        if key in result:
            raise ValueError('Duplicate authored ticket: ' + key)
        result['ticket-' + key] = (family, dict(region=region, city=city, street=street, house=house))
    if len(result) != 96:
        raise ValueError('Expected exactly 96 authored exercises')
    return result


AUTHORED = _rows()

# Explicit positive facts only. Unknown/no-injury statements remain in the full
# description; no keyword search mistakes «пострадавших нет» for an injury.
INJURED = set('01-2 01-3 03-3 05-3 07-1 10-1 12-3 13-3 15-2 15-3 16-2 17-2 17-3 19-3 20-3 21-1 21-2 23-1 23-2 24-2 24-3 30-2 31-2 32-2'.split())
NO_ACCESS = set('03-3 04-3 10-3 21-3 22-3'.split())
LANDMARKS = {
    '01-1': 'Депо около станции Москва-Пассажирская Киевская',
    '02-3': 'Набережная Яузы, напротив Большого Нижнего пруда',
    '04-3': 'Зеленоград, 9 микрорайон, корпус 902',
    '05-3': 'Стройка между улицами Центральная и 1-я Лесная',
    '06-3': 'Река Быковка, СНТ Малаховский городок',
    '06-1': 'Внутренняя сторона МКАД, 49 км, до съезда на улицу Генерала Дорохова',
    '08-2': 'Дорога через Крекшино между Киевским и Минским шоссе',
    '08-3': 'Озеро Круглое, пляж',
    '09-1': 'Станция метро Арбатская, Филевская линия',
    '09-2': 'Остановка «Кафе» напротив Бисерова озера',
    '09-3': 'Бережковский мост, середина моста, внутренняя сторона ТТК',
    '10-1': 'Станция Текстильщики, платформа в сторону области',
    '10-2': 'Люберецкие поля фильтрации',
    '11-3': 'Лес за деревней Барыкино',
    '12-2': 'Дорога между посёлками Октябрьский и Троицкий, водокачка',
    '13-1': 'Кусковский лесопарк, рядом со стадионом Фрезер',
    '13-2': 'Рублевское направление, после реки Чеченка, перед остановкой',
    '15-1': 'Лес за деревней Варварино',
    '15-2': 'Пруд справа от дома 17',
    '16-1': 'Станция Вялки, направление на посёлок Быково',
    '18-1': 'Дорога через Крекшино между Киевским и Минским шоссе',
    '19-1': 'Дорога Киреевск — Октябрьский, между заводом ЗОК и железной дорогой',
    '19-2': 'Свято-Иоанно-Богословский мужской монастырь, вход',
    '19-3': 'Платформа Лианозово, переход со стороны рынка',
    '20-2': 'Остановка Домодедовское кладбище, рядом с КПП',
    '21-2': 'Дорога Клин — Лотошино, дом напротив остановки у церкви',
    '21-3': 'Парковка ресторана WHEITE, Живописная бухта',
    '29-1': 'Парк Лосиный остров, вход от улицы Красной сосны',
    '29-2': 'Дорога Киреевск — Октябрьский, между заводом ЗОК и железной дорогой',
    '30-3': 'Микрорайон Солнечный',
}


def complete(draft, call, phones):
    from classifier import get_catalog, resolve
    from routing import main_services, preview
    scenario = draft['scenario']
    family, address = AUTHORED[draft['id']]
    owner, category, profile, working, result, result_fact = FAMILIES[family]
    card = dict(address)
    # These marked components are copied literally, never inferred from a nearby
    # landmark (e.g. apartment floor is not the burning floor in the situation).
    for field, marker in [('building', r'корп\.?'), ('structure', r'стр\.?'),
                          ('apartment', r'кв\.?'), ('entrance', r'под\.?'),
                          ('floor', r'эт\.?'), ('code', r'(?:код|домофон)')]:
        found = re.search(r'\b' + marker + r'\s*(\d+[А-Яа-яA-Za-z]?)\b', call['address'])
        if found:
            card[field] = found[1]
    card.update(caller_name=scenario['victim_name'], description=scenario['incident'],
                address_note=scenario['location'], incident_type=scenario['title'],
                phone=phones[0] if phones else '', services=[owner],
                service_phones={owner: '+7 900 000-00-01'})
    key = draft['id'].removeprefix('ticket-')
    card['object'] = LANDMARKS.get(key, '')
    card['injured'] = key in INJURED
    card['no_access'] = key in NO_ACCESS
    card['medical_help'] = family == 'medical' or key in INJURED
    card['offense'] = family in ('police', 'search_police', 'suspicious', 'death')
    # Keep already verified classifier/routing and address overlays intact.
    curated = scenario.get('prefilled_card') or {}
    card.update(curated)
    if key in LANDMARKS:
        card['object'] = LANDMARKS[key]
    # A booklet's first phrase may be age, a negative symptom or an entire
    # sentence, not an incident type. Use explicit neutral summaries instead.
    authored_types = {'07-3': 'Пропажа человека', '18-2': 'Нарушение сознания',
                      '28-3': 'Угроза падения табло'}
    if key in authored_types:
        card['incident_type'] = authored_types[key]
    annotation = ANNOTATED[key]
    version = get_catalog()['version']
    record = resolve(annotation['code'], version)
    owner = next(iter(main_services(record['main_service'])), owner)
    card.update(classifier_id=record['id'], classifier_version=version,
                classifier_group=record['group_id'], classifier_features=record['features'],
                incident_type=record['incident_type'])
    card['gasification'] = key == '04-1' or family == 'gas'
    card['construction_site'] = key == '05-3'
    card['tunnel'] = key == '27-2'
    card['threat_to_people'] = key in {'05-1', '06-3', '08-3', '09-3', '14-1', '21-3', '22-3', '24-3', '28-3', '29-3'}
    routed = preview(card)
    card['services'] = list(dict.fromkeys([owner, *(item['service'] for item in routed['suggestions'])]))
    card['service_phones'] = {owner: '+7 900 000-00-01'}
    working, result, result_fact = annotation['working'], annotation['result'], annotation['result_fact']
    crew = f"Бригада {draft['ticket']:02d}-{draft['call']}"
    source = f'Старший, {crew}'
    # The source incident is the context of every report; no fabricated worsening
    # of the caller's condition or unrelated building evacuation is injected.
    context = scenario['title'][:110]
    landmark = card.get('street') or card.get('object') or scenario['location'][:140]
    texts = [f'{crew} направлена на вызов «{context}». Место: {landmark}.',
             f'{crew} прибыла. Место: {landmark}. Приступаем к уточнению обстановки.', working, result]
    shift = ((draft['ticket'] - 1) % 3) * 10
    schedule = [40, 100 + shift, 160 + shift, 230 + 2 * shift]
    statuses = ['Начало реагирования', 'Прибытие', 'Проведение работ', 'Работы завершены']
    scenario.update(
        category_id=category, dds_profile=profile, owner_service=owner,
        prefilled_card=card,
        crew_options=[dict(id=crew, leader=f'Старший бригады {draft["ticket"]:02d}-{draft["call"]}',
                           phone=f'+7 900 000-{draft["ticket"]:02d}-0{draft["call"]}')],
        updates=[dict(id=key, after_seconds=seconds, source=source, text=text, unlocks_status=status)
                 for key, seconds, text, status in zip(
                     ['dispatched', 'arrived', 'working', 'done'], schedule, texts, statuses)],
        dds_expectation=dict(should_accept=True, brief_service=owner, expected_crew_id=crew,
                             update_response_limit_seconds=90, result_keywords=[result_fact],
                             brief_required_fields=[key for key in ('city', 'street', 'house', 'building',
                                                                    'structure', 'apartment', 'entrance', 'floor',
                                                                    'object', 'incident_type', 'injured')
                                                    if card.get(key)]),
        learning_objectives=('Подтвердить получение за 30 секунд, проверить адрес и ориентиры, '
            'назначить бригаду своей ДДС, передать доклад, отражать этапы только после сообщений '
            'старшего. Перед закрытием записать результат из заключительного доклада. '
            'Не добавлять службы и не изменять чужие статусы.'),
        description=(scenario['description'] + ' Продолжение ДДС: авторская учебная постановка; '
            'бригада, доклады, их сроки и результат добавлены для тренировки, а не взяты из билета. '
            'Карточка направлена в профильную ДДС территории происшествия. '
            '90 секунд на отражение доклада — настройка упражнения, не официальный норматив. '
            + CHOICES.get(key, '')),
    )
    # Reference fields are already present in DDS mode; useful also for the 112
    # fill mode. Never grade absent address components as if they were known.
    for field, label in [('city', 'Населённый пункт'), ('street', 'Улица'), ('house', 'Дом'),
                         ('building', 'Корпус'), ('structure', 'Строение'), ('apartment', 'Квартира'),
                         ('classifier_id', 'Код классификатора')]:
        if card.get(field):
            draft['rubric']['criteria'].append(dict(id='address_' + field, label=label, field=field,
                                                    mode='equals', expected=[card[field]], weight=1))
