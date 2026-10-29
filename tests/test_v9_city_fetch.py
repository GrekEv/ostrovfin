import csv
import io

import httpx
import pytest

from intelligence.city_fetch import CityFeedClient
from tests.test_v9_operator import notice


def data():
    buf=io.StringIO()
    writer=csv.DictWriter(buf,fieldnames=list(notice()))
    writer.writeheader()
    writer.writerow(notice())
    return buf.getvalue().encode()


def test_fetch_vetted_feed():
    def handler(request):
        assert request.url.host=='city.example.org'
        return httpx.Response(200,content=data(),headers={'content-type':'text/csv'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        notices=CityFeedClient('https://city.example.org/notices.csv','city.example.org',client).fetch()
        assert len(notices)==1
        assert notices[0]['source_reference']=='sochi-abc-100'


@pytest.mark.parametrize('url',[
    'http://city.example.org/notices.csv',
    'https://127.0.0.1/notices.csv',
    'https://localhost/notices.csv',
    'https://city.example.org:444/notices.csv',
    'https://user:pass@city.example.org/notices.csv',
    'https://elsewhere.example.org/notices.csv',
    'https://city.example.org/notices.xml',
])
def test_refuse_unapproved_sources(url):
    with pytest.raises(ValueError):
        CityFeedClient(url,'city.example.org')


def test_refuse_redirect_and_excessive_body():
    redirect=httpx.MockTransport(lambda request: httpx.Response(302,headers={'location':'https://elsewhere.example.org'}))
    with httpx.Client(transport=redirect) as client:
        with pytest.raises((ValueError,httpx.HTTPError)):
            CityFeedClient('https://city.example.org/notices.csv','city.example.org',client).fetch()
    huge=httpx.MockTransport(lambda request: httpx.Response(200,content=b'a'*2000001,headers={'content-type':'text/csv'}))
    with httpx.Client(transport=huge) as client:
        with pytest.raises(ValueError,match='size limit'):
            CityFeedClient('https://city.example.org/notices.csv','city.example.org',client).fetch()
