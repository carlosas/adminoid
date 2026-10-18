"""Browser acceptance test against a fake installation (test_fixture.py); never touches the real server.

Set PLAYWRIGHT_CHROMIUM to a Chromium executable if Playwright's bundled one is not installed.
"""
import os
import re
import secrets
import tempfile
import threading
from pathlib import Path

from playwright.sync_api import sync_playwright, expect
from waitress import create_server
from werkzeug.security import generate_password_hash

import settings as S
import configuration as C
import test_fixture as F
from common import write_json

def login(page, password, button):
    page.locator('[name=username]').fill('browser-test'); page.locator('[name=password]').fill(password)
    page.get_by_role('button', name=button, exact=True).click()
    page.locator('#application').wait_for(state='visible')

def main():
    with tempfile.TemporaryDirectory(prefix='pz-browser-') as temp:
        F.build(Path(temp))
        password = secrets.token_urlsafe(20)
        write_json(S.STATE / 'account.json', {'username': 'browser-test', 'hash': generate_password_hash(password), 'generation': 'test'})
        with F.fake_services(operation_delay=0.2) as fake:
            from app import create_app
            server = create_server(create_app(), host='127.0.0.1', port=0, threads=8)
            threading.Thread(target=server.run, daemon=True).start()
            base = f'http://127.0.0.1:{server.effective_port}'
            with sync_playwright() as playwright:
                options = {'headless': True, 'args': ['--disable-gpu', '--disable-dev-shm-usage']}
                if os.environ.get('PLAYWRIGHT_CHROMIUM'): options['executable_path'] = os.environ['PLAYWRIGHT_CHROMIUM']
                browser = playwright.chromium.launch(**options)
                page = browser.new_page(viewport={'width': 1360, 'height': 900})
                expect.set_options(timeout=10000)
                errors = []
                page.on('pageerror', lambda e: errors.append(str(e)))

                # Spanish login and overview
                page.goto(base + '/?lang=es')
                assert page.locator('html').get_attribute('lang') == 'es'
                login(page, password, 'Entrar')
                expect(page.locator('.sidebar .server-summary')).to_have_text('En línea · 3 jugadores')
                expect(page.locator('#pending-banner')).to_be_visible()
                expect(page.locator('#pending-text')).to_contain_text('SandboxVars')
                expect(page.locator('#player-list .item')).to_have_count(3)
                expect(page.locator('#activity-list li')).to_have_count(3)
                expect(page.locator('#overview-log > div').first).to_be_visible()
                expect(page.locator('[data-badge=mods]')).to_have_text('1')

                page.get_by_role('button', name='Mensaje a todos').click()
                page.locator('#dialog textarea[name=text]').fill('Reinicio en 5 minutos')
                page.locator('#dialog').get_by_role('button', name='Enviar').click()
                expect(page.locator('#toast')).to_have_text('Mensaje enviado.')
                assert fake['messages'] == ['Reinicio en 5 minutos'], fake['messages']

                page.get_by_role('button', name='Expulsar a Spiffo').click()
                page.locator('#dialog input[name=reason]').fill('afk')
                page.locator('#dialog').get_by_role('button', name='Expulsar').click()
                expect(page.locator('#player-list .item')).to_have_count(2)
                assert fake['kicked'] == ['Spiffo']

                # Settings: pending filter, typed controls, linked values review
                page.get_by_role('link', name='Ver cambios').click()
                expect(page.locator('#fields .row')).to_have_count(1)
                expect(page.locator('#fields .row')).to_contain_text('Población de zombis')
                page.locator('#categories').get_by_role('button', name=re.compile('^Zombis')).click()
                page.get_by_role('radio', name='Baja').click()
                expect(page.locator('#review-count')).to_have_text('1')
                expect(page.locator('[data-badge=settings]')).to_have_text('1')
                page.locator('#save-changes').click()
                expect(page.locator('#dialog')).to_contain_text('PopulationMultiplier')
                page.locator('#dialog').get_by_role('button', name='Confirmar y guardar').click()
                expect(page.locator('#review-count')).to_have_text('0')
                sandbox = C.Document('sandbox', C.read_all()['sandbox']).fields
                assert sandbox['Zombies'].value == 5 and sandbox['ZombieConfig.PopulationMultiplier'].value == 0.15

                page.locator('#search').fill('MinutesPerPage')
                expect(page.locator('#fields .row')).to_have_count(1)
                page.locator('#fields input.num').fill('0.5')
                page.get_by_role('button', name='Restablecer', exact=True).click()
                expect(page.locator('#review-count')).to_have_text('0')
                page.locator('#fields input.num').fill('0.5')
                page.get_by_role('button', name='Guardar 1 cambio').click()
                expect(page.locator('#review-count')).to_have_text('0')
                assert C.Document('sandbox', C.read_all()['sandbox']).fields['MinutesPerPage'].value == 0.5
                page.locator('#search').fill('')

                # Mods: add the missing dependency before its dependent and save through the review
                page.locator('.sidebar').get_by_role('link', name='Mods').click()
                expect(page.locator('#mod-issues')).to_contain_text('tsarslib')
                page.get_by_role('button', name='Añadir tsarslib').click()
                expect(page.locator('#load-order .order-item').nth(2)).to_contain_text('tsarslib')
                expect(page.locator('[data-badge=mods]')).to_be_hidden()
                page.get_by_role('link', name='Revisar y guardar').click()
                page.locator('#save-changes').click()
                expect(page.locator('#review-count')).to_have_text('0')
                mods = C.Document('server', C.read_all()['server']).fields['Mods'].value
                assert '\\tsarslib;\\BetterCarPhysics' in mods, mods

                # Maintenance: restart with step progress, then a restore that needs the server name
                page.locator('.sidebar').get_by_role('link', name='Mantenimiento').click()
                page.locator('#view-maintenance').get_by_role('button', name='Reiniciar…').click()
                expect(page.locator('#dialog .names span')).to_have_count(2)
                page.locator('#dialog').get_by_role('button', name='Guardar y reiniciar').click()
                expect(page.locator('#operation-card')).to_contain_text('Reinicio completado')
                expect(page.locator('#operation-card .steps li.done')).to_have_count(3)
                # The restart applied the saved settings, so nothing stays pending without reloading
                page.locator('.sidebar').get_by_role('link', name='Configuración').click()
                expect(page.locator('#settings-filter')).to_contain_text('Pendientes de reinicio')
                expect(page.locator('#settings-filter')).not_to_contain_text('Pendientes de reinicio ·')
                page.locator('.sidebar').get_by_role('link', name='Mantenimiento').click()
                page.locator('#backup-rows').get_by_role('button', name=re.compile('^Restaurar zomboid-')).first.click()
                confirm = page.locator('#dialog').get_by_role('button', name='Restaurar y reiniciar')
                expect(confirm).to_be_disabled()
                page.locator('#dialog input[name=confirm]').fill(S.NAME)
                expect(confirm).to_be_enabled()
                confirm.click()
                expect(page.locator('#operation-card')).to_contain_text('Restauración completada')

                # Logs: level filters, search highlight, pause
                page.locator('.sidebar').get_by_role('link', name='Registros').click()
                expect(page.locator('#log-lines .ln').first).to_be_visible()
                page.locator('#log-levels').get_by_role('button', name=re.compile('^Errores')).click()
                levels = page.locator('#log-lines .ln > span:nth-child(3)').all_inner_texts()
                assert levels and set(levels) == {'ERROR'}, levels
                page.locator('#log-levels').get_by_role('button', name='Todo').click()
                page.locator('#log-search').fill('Kate')
                expect(page.locator('#log-lines mark').first).to_have_text('Kate')
                page.get_by_role('button', name='Pausar').click()
                expect(page.locator('#log-live')).to_contain_text('En pausa')
                page.screenshot(path=str(Path(tempfile.gettempdir()) / 'zomboid-panel-logs.png'))

                # English, phone layout and logout
                page.locator('.sidebar .lang-switch a[data-lang=en]').click()
                page.wait_for_url('**/?lang=en#logs')
                assert page.locator('html').get_attribute('lang') == 'en'
                expect(page.get_by_role('heading', name='Logs')).to_be_visible()
                page.goto(base + '/?lang=en#overview')
                expect(page.get_by_role('heading', name='Overview')).to_be_visible()
                page.set_viewport_size({'width': 390, 'height': 844})
                expect(page.locator('.tabbar')).to_be_visible()
                expect(page.locator('.sidebar')).to_be_hidden()
                page.locator('#menu-button').click()
                page.locator('#menu-sheet').get_by_role('button', name='Log out').click()
                expect(page.locator('#login-panel')).to_be_visible()
                login(page, password, 'Log in')
                expect(page.locator('.tabbar a[aria-current=page]')).to_have_text('Overview')
                assert not errors, errors
                browser.close()
            server.close()
    print('Browser OK: Spanish/English, overview, message, kick, settings review, pending filter, mods dependency fix, '
          'restart progress, restore confirmation, live logs, phone layout and logout.')

if __name__ == '__main__': main()
