import json
import sqlite3

import pytest
from fastapi import HTTPException

import maps


@pytest.fixture
def package(tmp_path, monkeypatch):
    monkeypatch.setenv('MAP_DATA_DIR', str(tmp_path))
    db = sqlite3.connect(tmp_path/'regional.sqlite')
    db.executescript('''CREATE TABLE metadata(key TEXT,value TEXT);
        CREATE TABLE features(id INTEGER PRIMARY KEY,kind TEXT,name TEXT,min_zoom INTEGER,geometry TEXT);
        CREATE VIRTUAL TABLE bounds USING rtree(id,west,east,south,north);
        CREATE TABLE addresses(id INTEGER PRIMARY KEY,label TEXT,latitude REAL,longitude REAL);
        CREATE VIRTUAL TABLE address_search USING fts5(label,content='addresses',content_rowid='id');''')
    db.execute('INSERT INTO metadata VALUES(?,?)', ('manifest',json.dumps({'title':'Москва и область'})))
    db.execute('INSERT INTO features VALUES(?,?,?,?,?)',(1,'road','Лесная',8,'[[37,55],[38,56]]'))
    db.execute('INSERT INTO bounds VALUES(1,37,38,55,56)')
    db.execute("INSERT INTO addresses VALUES(1,'Химки, Лесная улица, 7',55.89,37.44)")
    db.execute("INSERT INTO address_search(address_search) VALUES('rebuild')")
    db.commit()
    db.close()


def test_regional_queries(package):
    assert maps.manifest()['title']=='Москва и область'
    assert len(maps.features(36,54,39,57,15)['features'])==1
    assert maps.features(30,50,31,51,15)['features']==[]
    assert maps.search('Химки ул Лесная дом 7')['results'][0]['latitude']==55.89
    assert maps.search('" OR *')['results']==[]
    assert maps.features(36,54,39,57,12)['features'][0]['name']=='Лесная'
    assert maps.features(30,50,31,51,12)['features']==[]


def test_bounds_and_missing_package(package, monkeypatch, tmp_path):
    with pytest.raises(HTTPException) as error:
        maps.features(float('nan'),54,39,57,15)
    assert error.value.status_code==422
    monkeypatch.setenv('MAP_DATA_DIR',str(tmp_path/'missing'))
    with pytest.raises(HTTPException) as error:
        maps.manifest()
    assert error.value.status_code==503


def test_house_number_is_not_a_prefix_or_street_number(package, tmp_path):
    with sqlite3.connect(tmp_path/'regional.sqlite') as db:
        db.execute("INSERT INTO addresses VALUES(2,'Химки, Лесная улица, 70',55.89,37.44)")
        db.execute("INSERT INTO addresses VALUES(3,'Химки, 7-я Лесная улица, 15',55.89,37.44)")
        db.execute("INSERT INTO address_search(address_search) VALUES('rebuild')")
    found = maps.search('Химки Лесная 7')['results']
    assert [item['label'] for item in found] == ['Химки, Лесная улица, 7']
