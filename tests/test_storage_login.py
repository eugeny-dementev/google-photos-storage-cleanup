from types import SimpleNamespace
from unittest.mock import MagicMock
from gpsync.auth import login_with_browser
from gpsync.config import load_settings


def test_ssh_login_waits_for_marker_without_reading_eof(tmp_path,monkeypatch):
    settings=load_settings(data_dir=tmp_path/'state')
    settings.data_dir.mkdir(parents=True)
    (settings.data_dir/'network.json').write_text('{"proxy":"http://127.0.0.1:3128","browser_proxy":"socks5h://127.0.0.1:1180"}')
    marker=tmp_path/'ready' 
    page=SimpleNamespace(goto=lambda *a,**k:None,wait_for_timeout=lambda n:marker.touch())
    closed=[]
    context=SimpleNamespace(pages=[page],cookies=lambda:[],close=lambda:closed.append(True))
    launches=[]
    def launch(**kwargs): launches.append(kwargs);return context
    runtime=SimpleNamespace(chromium=SimpleNamespace(launch_persistent_context=launch))
    monkeypatch.setenv('GPSYNC_BROWSER_CHANNEL','chrome')
    manager=MagicMock();manager.__enter__.return_value=runtime
    monkeypatch.setattr('playwright.sync_api.sync_playwright',lambda:manager)
    monkeypatch.setattr('gpsync.auth._require_display',lambda:None)
    monkeypatch.setattr('sys.stdin',SimpleNamespace(readline=lambda:(_ for _ in ()).throw(AssertionError('SSH stdin must not end login'))))
    login_with_browser(settings,ready_file=marker)
    assert closed==[True]
    assert launches[0]['channel']=='chrome'
    assert launches[0]['proxy']=={'server':'socks5://127.0.0.1:1180'}
    assert settings.cookies_path.exists()
    assert not marker.exists()
