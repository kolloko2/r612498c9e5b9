"""Two synthetic, teacher-editable exercises for the complete DDS work cycle."""
from copy import deepcopy


def scenarios() -> list[dict]:
    result = []
    examples = [
        ('waste', 'Мусор в неположенном месте', 'Мусор во дворе',
         'Во дворе рядом с контейнерной площадкой оставлены мешки с бытовым мусором. Пострадавших нет.',
         'Управляющая организация согласовала уборку. Крупногабаритных и опасных отходов нет.',
         'Контейнерная площадка доступна. Для уборки достаточно штатного инвентаря.',
         'Бригада прибыла. Обнаружены мешки с бытовым мусором рядом с контейнерной площадкой.',
         'Бригада собирает мусор и очищает площадку.',
         'Мусор убран, площадка очищена, работы завершены.', ['мусор', 'убран'],
         ['мусор'], ['мусор']),
        ('entrance', 'Нет доступа в подъезд', 'Нет доступа в подъезд',
         'Дверь подъезда не открывается, жители не могут войти. Людей, которым нужна срочная помощь, нет.',
         'Доступ к техническому помещению согласован. Ключ передаст дежурный управляющей организации.',
         'Предполагается неисправность замка. Точная причина станет известна после осмотра.',
         'Бригада прибыла. Подтверждена неисправность замка входной двери.',
         'Бригада ремонтирует замок входной двери.',
         'Замок исправен, доступ в подъезд восстановлен, работы завершены.', ['доступ', 'восстановлен'],
         ['доступ'], ['дверь']),
    ]
    for index, (code, title, incident, description, organization, specialist, arrived, working, done,
                final_keywords, organization_keywords, specialist_keywords) in enumerate(examples, 1):
        crew_id = 'Учебная бригада ' + str(index)
        contacts = [
            dict(id='chief', name='Начальник смены', role='superior', number=f'8{index}01'),
            dict(id='organization', name='Руководитель управляющей организации', role='organization_head',
                 number=f'8{index}02', facts=[organization], required_keywords=organization_keywords, required=True),
            dict(id='leader', name='Старший учебной бригады', role='crew_leader', number=f'8{index}03', crew_id=crew_id),
            dict(id='specialist', name='Специалист учебной бригады', role='crew_specialist', number=f'8{index}04',
                 crew_id=crew_id, facts=[specialist], required_keywords=specialist_keywords, required=True),
        ]
        updates = [
            dict(id='departed', after_seconds=5, source='Старший учебной бригады',
                 text='Бригада выехала на улицу Учебную, дом ' + str(index) + '.', unlocks_status='Начало реагирования'),
            dict(id='arrived', after_seconds=10, source='Старший учебной бригады', text=arrived, unlocks_status='Прибытие'),
            dict(id='working', after_seconds=15, source='Старший учебной бригады', text=working, unlocks_status='Проведение работ'),
            dict(id='done', after_seconds=20, source='Старший учебной бригады', text=done, unlocks_status='Работы завершены'),
        ]
        result.append(dict(
            id='simple_dds_' + code, title='ДДС: ' + title, category_id='other', difficulty='basic', dds_profile='general',
            description='Простая учебная ситуация: полный цикл решения диспетчера ДДС.',
            learning_objectives='Принять карточку, выбрать бригаду, согласовать действия, отразить доклады статусами и комментариями, доложить начальнику и завершить работу.',
            victim_name='Учебный заявитель', incident=incident, location=f'Москва, улица Учебная, дом {index}',
            known_facts=[description], emotion='Спокойно', behavior='Учебная ситуация', opening=description,
            owner_service='Деп. ЖКХ', text_input_allowed=True,
            crew_options=[dict(id=crew_id, leader='Старший учебной бригады', phone=f'8{index}03')],
            phone_contacts=contacts,
            prefilled_card=dict(city='Москва', street='Учебная', house=str(index), incident_type=incident,
                                description=description, injured=False, services=['Деп. ЖКХ'],
                                service_phones={'Деп. ЖКХ': f'8{index}01'}),
            updates=updates,
            dds_expectation=dict(should_accept=True, brief_service='Деп. ЖКХ', expected_crew_id=crew_id,
                                 brief_required_fields=['city', 'street', 'house', 'incident_type'],
                                 result_keywords=final_keywords,
                                 update_keywords={'departed': ['выехала'], 'arrived': ['прибыла'],
                                                  'working': ['мусор'] if code == 'waste' else ['замок'],
                                                  'done': final_keywords}, pass_percent=100)))
    return deepcopy(result)
