"""Bounded, non-secret deployment settings; shared with the host worker."""
import ipaddress
import re
import xml.etree.ElementTree as ET


DEFAULTS = {'sip_external_address':'127.0.0.1', 'sip_local_net':'172.16.0.0/12',
            'allowed_extensions':'201', 'max_calls':20, 'postgres_max_connections':100,
            'postgres_shared_buffers_mb':128, 'backend_memory_mb':1024,
            'voice_memory_mb':2048, 'backend_cpus':2, 'voice_cpus':2,
            'llm_profile':'mock', 'llm_device':'auto'}
ENV_KEYS = {'sip_external_address':'SIP_EXTERNAL_ADDRESS','sip_local_net':'SIP_LOCAL_NET',
            'allowed_extensions':'ALLOWED_EXTENSIONS','max_calls':'MAX_CALLS',
            'postgres_max_connections':'POSTGRES_MAX_CONNECTIONS',
            'postgres_shared_buffers_mb':'POSTGRES_SHARED_BUFFERS_MB',
            'backend_memory_mb':'BACKEND_MEMORY_MB','voice_memory_mb':'VOICE_MEMORY_MB',
            'backend_cpus':'BACKEND_CPUS','voice_cpus':'VOICE_CPUS',
            'llm_profile':'LLM_PROFILE', 'llm_device':'LLM_DEVICE'}
LIMITS = {'max_calls':(1,100),'postgres_max_connections':(20,500),
          'postgres_shared_buffers_mb':(64,8192),'backend_memory_mb':(256,32768),
          'voice_memory_mb':(512,65536),'backend_cpus':(1,64),'voice_cpus':(1,64)}


def validate(values):
    if not isinstance(values,dict) or set(values)-set(DEFAULTS):
        raise ValueError('Неизвестные параметры конфигурации')
    result={}
    for key,value in values.items():
        if key in LIMITS:
            low,high=LIMITS[key]
            if type(value) is not int or not low<=value<=high:
                raise ValueError(f'{key}: требуется целое число {low}–{high}')
        elif key=='sip_external_address':
            ipaddress.ip_address(value)
        elif key=='sip_local_net':
            ipaddress.ip_network(value,strict=False)
        elif key=='llm_profile':
            from llm import PROFILES
            if value not in PROFILES:
                raise ValueError('Неизвестный профиль модели: ' + ', '.join(PROFILES))
        elif key=='llm_device':
            from llm import DEVICES
            if value not in DEVICES:
                raise ValueError('Неизвестное устройство модели: ' + ', '.join(DEVICES))
        elif key=='allowed_extensions':
            if not isinstance(value,str) or not re.fullmatch(r'\d{2,8}(,\d{2,8}){0,99}',value):
                raise ValueError('Внутренние номера: только цифры через запятую')
        result[key]=value
    return result


def parse_xml(source):
    if not isinstance(source,str) or len(source.encode('utf-8'))>32768:
        raise ValueError('XML не должен превышать 32 КиБ')
    if '<!DOCTYPE' in source.upper() or '<!ENTITY' in source.upper():
        raise ValueError('DTD и внешние сущности запрещены')
    try: root=ET.fromstring(source)
    except ET.ParseError: raise ValueError('Некорректный XML') from None
    if root.tag!='trainer112-settings' or root.attrib!={'version':'1'}:
        raise ValueError('Ожидается trainer112-settings version="1"')
    values={}
    for item in root:
        key=item.get('key')
        if item.tag!='setting' or list(item) or set(item.attrib)!={'key'} or key in values or key not in DEFAULTS:
            raise ValueError('Неизвестный или повторяющийся элемент XML')
        value=item.text or ''
        if key in LIMITS:
            if not re.fullmatch(r'\d{1,6}',value):raise ValueError('Некорректное целое число')
            value=int(value)
        values[key]=value
    if not values:raise ValueError('Нет параметров для импорта')
    return validate(values)


def export_xml(values):
    root=ET.Element('trainer112-settings',version='1')
    for key,value in validate(values).items():
        ET.SubElement(root,'setting',key=key).text=str(value)
    return ET.tostring(root,encoding='unicode',xml_declaration=True)
