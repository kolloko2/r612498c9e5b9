import sqlite3

from fastapi import FastAPI
from fastapi.testclient import TestClient

from accounts import Accounts
from directory_routes import router


def test_explicit_mapping_role_boundaries_and_login():
    store=type('Store',(),{'db':sqlite3.connect(':memory:',check_same_thread=False)})()
    accounts=Accounts(store)
    admin=accounts.bootstrap('admin','long-test-password','Admin')
    learner=accounts.create_user('learner','long-test-password','Learner','student')
    called=[]
    def authenticate(name,password):
        called.append(name)
        return name=='directory.user' and password=='directory-password'
    app=FastAPI()
    app.include_router(router(accounts,lambda:None,authenticate,lambda:True))
    client=TestClient(app)
    auth={'X-User-Session':admin['session_token']}
    credentials={'username':'directory.user','password':'directory-password'}
    assert client.post('/api/v1/auth/directory-login',json=credentials).status_code==401
    assert not called
    assert client.post('/api/v1/admin/users/'+admin['user']['id']+'/directory',json={'username':'directory.user'},headers=auth).status_code==403
    assert client.post('/api/v1/admin/users/'+learner['id']+'/directory',json={'username':'directory.user'},headers=auth).status_code==200
    login=client.post('/api/v1/auth/directory-login',json=credentials)
    assert login.status_code==200
    assert login.json()['user']['role']=='student'
    accounts.set_active(learner['id'],False)
    assert client.post('/api/v1/auth/directory-login',json=credentials).status_code==401
    assert called==['directory.user']
    assert store.db.execute('SELECT failures FROM account_login_failures WHERE username=?',('directory.user',)).fetchone()[0]==1
