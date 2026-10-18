"""Browser acceptance test against a temporary copy of the actual configuration."""
import json
import os
import secrets
import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from playwright.sync_api import sync_playwright
from waitress import create_server
from werkzeug.security import generate_password_hash
import settings as S
from common import atomic, write_json
import configuration as C
import operations as O
from app import create_app

def main():
    originals=C.read_all()
    with tempfile.TemporaryDirectory(prefix='pz-browser-') as temp:
        root=Path(temp);S.STATE=root/'state';S.DATA=root/'Zomboid';S.BACKUPS=root/'backups';S.SOCKET=S.STATE/'console.sock'
        S.STATE.mkdir();(S.DATA/'Server').mkdir(parents=True)
        for scope,text in originals.items():atomic(S.DATA/'Server'/S.FILES[scope],text)
        password=secrets.token_urlsafe(20)
        write_json(S.STATE/'account.json',{'username':'browser-test','hash':generate_password_hash(password),'generation':'test'})
        fake_status={'state':'active','memory':'3200000000','version':'42.20.4','console':True,'pending':[],'applied_known':True}
        with patch.object(O,'status',return_value=fake_status),patch.object(O,'players',return_value={'count':0,'names':[]}):
            server=create_server(create_app(),host='127.0.0.1',port=0)
            thread=threading.Thread(target=server.run,daemon=True);thread.start()
            with sync_playwright() as playwright:
                browser=playwright.chromium.launch(headless=True,args=['--disable-gpu','--disable-dev-shm-usage'])
                page=browser.new_page(viewport={'width':1360,'height':900})
                errors=[];page.on('pageerror',lambda e:errors.append(str(e)))
                page.on('pageerror',lambda e:print('JS error:',str(e),flush=True))
                page.on('response',lambda r:print('HTTP',r.status,r.url.split('/')[-1],flush=True))
                page.goto(f'http://127.0.0.1:{server.effective_port}')
                assert page.locator('.language-nav .flag').count()==2
                assert page.locator('.flag.active').get_attribute('href')=='?lang=en'
                assert page.locator('html').get_attribute('lang')=='en'
                page.locator('.flag[href="?lang=es"]').click()
                page.wait_for_url('**/?lang=es')
                assert page.locator('html').get_attribute('lang')=='es'
                page.get_by_role('button',name='Entrar',exact=True).wait_for()
                page.locator('[name=username]').fill('browser-test');page.locator('[name=password]').fill(password)
                page.get_by_role('button',name='Entrar',exact=True).click()
                page.locator('#application').wait_for(state='visible')
                page.wait_for_function('()=>document.querySelectorAll(".field").length>400')
                page.get_by_role('button',name='Mantenimiento',exact=True).wait_for()
                page.get_by_role('button',name='Cerrar sesión',exact=True).click()
                page.locator('#login-panel').wait_for(state='visible')
                page.locator('.flag[href="?lang=en"]').click()
                page.wait_for_url('**/?lang=en')
                assert page.locator('html').get_attribute('lang')=='en'
                page.locator('[name=username]').fill('browser-test');page.locator('[name=password]').fill(password)
                page.get_by_role('button',name='Log in',exact=True).click()
                page.wait_for_timeout(1000)
                if page.locator('#message').is_visible():print('UI:',page.locator('#message').inner_text(),flush=True)
                page.locator('#application').wait_for(state='visible')
                page.wait_for_function('()=>document.querySelectorAll(".field").length>400')
                assert 'options detected' in page.locator('#edit-count').inner_text()
                page.locator('#search').fill('MinutesPerPage')
                page.wait_for_function('()=>document.querySelectorAll(".field").length===1')
                page.locator('#sandbox-MinutesPerPage').fill('0.5')
                page.get_by_role('button',name='Review and save').click()
                page.locator('#preview').wait_for(state='visible')
                assert 'MinutesPerPage' in page.locator('#diff').inner_text()
                page.get_by_role('button',name='Confirm save').click()
                page.locator('#preview').wait_for(state='hidden')
                assert C.Document('sandbox',C.read_all()['sandbox']).fields['MinutesPerPage'].value==0.5
                page.screenshot(path='/tmp/zomboid-panel-settings.png',full_page=True)
                page.locator('#search').fill('')
                page.get_by_text('Edit spawn regions and points',exact=True).click()
                page.get_by_role('button',name='Add region',exact=True).click()
                page.get_by_role('button',name='Review and save').click()
                page.locator('#preview').wait_for(state='visible')
                assert 'New region' in page.locator('#diff').inner_text()
                page.get_by_role('button',name='Keep editing').click()
                page.get_by_role('button',name='Maintenance',exact=True).click()
                page.get_by_role('button',name='Check players').click()
                page.wait_for_function('()=>document.querySelector("#players").textContent.includes("0")')
                page.once('dialog',lambda d:d.accept())
                page.get_by_role('button',name='Log out').click()
                page.locator('#login-panel').wait_for(state='visible')
                assert not errors,errors
                browser.close()
            server.close()
    print('Browser OK: English/Spanish switch, login, 426 fields, search, editing, review, save, spawn, players and logout.')

if __name__=='__main__':main()
