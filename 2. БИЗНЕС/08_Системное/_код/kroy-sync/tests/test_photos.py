import hashlib,base64
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.api import create_app
from app.db import Item,transaction
from app.photos import photo_key,photo_preview
from test_app import setup


def test_cached_photo_and_same_color_preview_keep_source(monkeypatch,tmp_path,setup):
    engine,u,cmd=setup;monkeypatch.setenv('KROY_PHOTO_DIR',str(tmp_path))
    source='https://basket-1.wbbasket.ru/example.jpg'
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDuoAAAAASUVORK5CYII=')
    (tmp_path/(photo_key(source)+'.img')).write_bytes(png)
    with transaction(engine) as s:
        i=s.get(Item,'s');i.photo=source;i.color='Бордо'
        s.add(Item(id='same-color',name='Размер L',model=i.model,color=i.color,size='L',kind='garment',unit='piece',photo='',source={},barcodes=[]))
        s.add(Item(id='other-color',name='Чёрный',model=i.model,color='Чёрный',size='S',kind='garment',unit='piece',photo='',source={},barcodes=[]))
    client=TestClient(create_app(engine));response=client.get('/api/photos/'+photo_key(source))
    assert response.status_code==200 and response.content==png
    assert response.headers['content-type']=='image/png'
    assert 'immutable' in response.headers['cache-control']
    assert client.get('/api/photos/'+'0'*64).status_code==404
    assert client.get('/api/photos/not-a-hash').status_code==404
    with transaction(engine) as s:
        from app.db import User
        s.get(User,'owner').token_hash=hashlib.sha256(b'photo-test').hexdigest()
    items={i['id']:i for i in client.get('/api/catalog',headers={'Authorization':'Bearer photo-test'}).json()['items']}
    assert items['s']['photo']==source
    assert items['same-color']['photo']==''
    assert items['same-color']['photo_preview']==photo_preview(source)
    assert items['other-color']['photo_preview']==''


def test_public_image_cache_rejects_unapproved_hosts():
    from app.photos import cacheable,cache_photo
    for url in ['http://basket-1.wbbasket.ru/a','https://basket-1.wbbasket.ru.evil.test/a','https://127.0.0.1/a','https://basket-1.wbbasket.ru:8000/a','https://user:pass@basket-1.wbbasket.ru/a']:
        assert not cacheable(url)
        assert cache_photo(url) is None
    assert cacheable('https://basket-41.wbbasket.ru/vol1/product/images/1.webp')


def test_download_cache_is_atomic_and_rejects_non_images(monkeypatch,tmp_path):
    import io,urllib.request,pytest
    from app.photos import cache_photo
    monkeypatch.setenv('KROY_PHOTO_DIR',str(tmp_path))
    png=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jDuoAAAAASUVORK5CYII=')
    calls=[]
    class Opener:
        def open(self,request,timeout):
            calls.append(request.full_url)
            return io.BytesIO(png if request.full_url.endswith('good') else b'<html>error</html>')
    monkeypatch.setattr(urllib.request,'build_opener',lambda *args:Opener())
    url='https://basket-1.wbbasket.ru/good'
    path=cache_photo(url)
    assert path.read_bytes()==png
    assert cache_photo(url)==path and calls==[url]
    bad='https://basket-1.wbbasket.ru/bad'
    with pytest.raises(ValueError,match='Invalid'):cache_photo(bad)
    assert not (tmp_path/(photo_key(bad)+'.img')).exists()
